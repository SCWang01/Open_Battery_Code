# -*- coding: utf-8 -*-
"""
Created on Apr 22nd, 2025

@author:gcg

Variant V5: an updated version of V3.  V3 collapses the entire CAISO battery
fleet into a single equivalent battery and assumes 100% of it bids optimally
through one aggregated demand function, which is unrealistic.  V5 splits the
aggregate fleet with a ratio k in (0, 1]:

  * a controllable k-unit (capacity Cap*k, power Pdmax*k / Pcmax*k) is optimised
    by the bidding method (M1), and
  * the remaining (1-k) portion keeps its original actual output,
    (1-k) * hourly_battery.

The combined "method" output is therefore
    P_method = P_optimised(k-unit) + (1-k) * hourly_battery,
compared against the full actual fleet (100% hourly_battery) baseline.  At k=1
this reproduces V3 exactly.  Data source and cost path are identical to V3:
the hourly CAISO natural-gas data in ng_data/gasYYYYMM.xlsx with the 'exact'
piecewise merit-order cost model.
"""

import calendar
import json
import random
from pathlib import Path

import gurobipy as gp
import numpy as np
import pandas as pd
from gurobipy import GRB
from tqdm import tqdm

from cost_calculation import gas_cost, gas_marginal_price

#%% Global settings

random.seed(42)  # set the random seed
np.random.seed(42)  # set the random seed
N_t = 24  # The number of time intervals in one day
START_YEAR_MONTH = (2023, 1)
END_YEAR_MONTH = (2025, 12)
year_month_list = [
    (year, month)
    for year in range(START_YEAR_MONTH[0], END_YEAR_MONTH[0] + 1)
    for month in range(1, 13)
    if START_YEAR_MONTH <= (year, month) <= END_YEAR_MONTH
]
monthlist = np.array([calendar.month_name[month] for _, month in year_month_list])
monthnumlist = np.array([f'{month:02d}' for _, month in year_month_list])
eta = 0.95 # Set the efficiency of the equivalent battery
Smax = 1 # Set the maximum SOC of the equivalent battery
Smin = 0 # Set the minimum SOC of the equivalent battery
N_price = 100 # the number of traversed prices
meanstd = 2 # prediction error variable
k = 0.2 # fraction of the fleet treated as the controllable (optimised) unit; must be in (0, 1]
PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PROGRAM_DIR.parent
DATA_DIR = PROJECT_ROOT / 'data'
RESULTS_DIR = PROJECT_ROOT / 'Results'
COST_MODE = 'exact' # gas cost model: 'exact' (piecewise merit order) or 'quadratic' (Fuel_Coe.xlsx fit)


def ensure_results_dir():
    """Create and return the project-level Results directory."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


#%% Read CAISO data

def read_monthly_natural_gas(year, monthnum, Nday):
    """Read the existing hourly monthly natural-gas workbook."""
    input_path = DATA_DIR / 'ng_data' / f'gas{year}{monthnum}.xlsx'
    monthly_gas = pd.read_excel(input_path, usecols=['date', 'hour', 'gas.gas'])
    monthly_gas['date'] = pd.to_numeric(monthly_gas['date'], errors='coerce')
    monthly_gas['hour'] = pd.to_numeric(monthly_gas['hour'], errors='coerce')
    monthly_gas['gas.gas'] = pd.to_numeric(monthly_gas['gas.gas'], errors='coerce')
    monthly_gas = monthly_gas.dropna(subset=['date', 'hour', 'gas.gas'])
    monthly_gas[['date', 'hour']] = monthly_gas[['date', 'hour']].astype(int)

    gas_by_time = monthly_gas.set_index(['date', 'hour'])['gas.gas']
    if gas_by_time.index.has_duplicates:
        raise ValueError(f'Duplicate date/hour rows found in {input_path}')

    expected_dates = [int(f'{year}{monthnum}{day:02d}') for day in range(1, Nday + 1)]
    expected_index = pd.MultiIndex.from_product(
        [expected_dates, range(N_t)], names=['date', 'hour']
    )
    gas_by_time = gas_by_time.reindex(expected_index)
    skipped_dates = [
        str(date) for date in expected_dates
        if gas_by_time.loc[date].isna().any()
    ]
    return gas_by_time.fillna(0).to_numpy(dtype=float), skipped_dates

def readdata(Ntall,Nt,Nday):
    df_price = pd.read_csv(DATA_DIR / 'price' / f'{year}{monthnum} CAISO Average Price.csv')  # real time average LMP
    price = df_price['price'].values
    hourly_gas, skipped_gas_dates = read_monthly_natural_gas(year, monthnum, Nday)
    battery = np.zeros((Ntall*Nday))  # real time battery discharge power (>0 discharge;<0 charge)
    for d in range(Nday): # cut the data by days
        date = monthnum + f'{d + 1:02d}'
        if f'{year}{date}' in skipped_gas_dates:
            continue

        try:
            df_ess = pd.read_csv(DATA_DIR / 'battery_data' / f'CAISO-batteries-{year}{date}.csv')  # read the real time battery power
            df_ess = df_ess.set_index(df_ess.columns[0])
            daily_battery = pd.to_numeric(
                df_ess.loc['Total batteries', :], errors='raise'
            ).to_numpy(dtype=float)
            if len(daily_battery) != Ntall:
                raise ValueError(f'Expected {Ntall} battery values for {year}{date}')
            if not np.isfinite(daily_battery).all():
                raise ValueError(f'Non-finite battery values for {year}{date}')
            battery[Ntall*d:Ntall*(d+1)] = daily_battery
        except (OSError, pd.errors.ParserError, KeyError, ValueError):
            # The original data set contains truncated and incomplete CAISO days.
            pass

    df_cu = pd.read_csv(DATA_DIR / 'curtailment' / f'curtailment_{year}{monthnum}.csv')  # read the curtailment of renewables
    curtailment = df_cu['total_curtailment_mwh'].to_numpy()[:Nt*Nday]
    if len(curtailment) < Nt*Nday:
        # Keep the calendar alignment when a source month is short (March
        # 2025 contains 743 rather than 744 hourly values).
        curtailment = np.pad(curtailment, (0, Nt*Nday-len(curtailment)))

    # transform data sampled per 5 minutes to data sampled per hour
    points_per_hour = 12
    n_hours_price = len(price) // points_per_hour
    n_hours_battery = len(battery) // points_per_hour
    price = price[:n_hours_price * points_per_hour]
    battery = battery[:n_hours_battery * points_per_hour]
    hourly_price = price.reshape(n_hours_price, points_per_hour).mean(axis=1)
    hourly_battery = battery.reshape(n_hours_battery, points_per_hour).mean(axis=1)

    # Use the same monthly power capacity on every valid day, but set it to
    # zero on missing/invalid days whose ESS output is all zero.
    daily_battery = hourly_battery.reshape(Nday, Nt)
    monthly_Pdmax = np.ceil(hourly_battery.max()/100)*100
    zero_output_days = np.all(daily_battery == 0, axis=1)
    Pdmax = np.full(Nday, monthly_Pdmax, dtype=float)
    Pdmax[zero_output_days] = 0
    Pcmax = Pdmax.copy()

    # calculate the capacity and the SOC at 0:00 on the first day of the month
    E = np.zeros((Nt*Nday+1))
    for i in range(1,Nt*Nday+1):
        if  hourly_battery[i-1] < 0:
            E[i] = E[i-1] -  hourly_battery[i-1]*eta
        else:
            E[i] = E[i-1] -  hourly_battery[i-1]/eta
    Emax = max(E)
    Emin = min(E)
    Cap = 1000 *np.ceil((Emax-Emin)/1000)
    SOC = E/Cap
    smin_oringinal = np.min(SOC)
    if smin_oringinal<0:
        SINI = np.ceil(-smin_oringinal/0.05)*0.05
    else:
        SINI = 0
    return hourly_price, hourly_gas, hourly_battery, Pdmax, Pcmax, Smax, Smin, Cap, eta, SINI, curtailment, skipped_gas_dates


#%% M1: the demand function bidding -- the proposed method
def build_dsfunction(pri0, Res0, numncd0):
    """Build price stairs enriched with representative NCD and SOC-limit values.

    Consecutive price samples stay in the same stair only while the rounded
    power, the sign of (ncd0 - ncd1), and the SOC-limit state all remain
    unchanged.  Each stair stores the most frequent NCD/SOC vector in its
    interval; ties use the vector that first appears at the lower price.
    """
    Res0_rounded = np.round(Res0, 4)
    sign_state = np.sign(numncd0[0, :] - numncd0[1, :]).astype(int)
    soc_limit_state = numncd0[3, :].astype(int)

    segment_bounds = []
    segment_start = 0
    for K in range(1, len(Res0_rounded) + 1):
        segment_finished = (
            K == len(Res0_rounded)
            or Res0_rounded[K] != Res0_rounded[segment_start]
            or sign_state[K] != sign_state[segment_start]
            or soc_limit_state[K] != soc_limit_state[segment_start]
        )
        if segment_finished:
            segment_bounds.append((segment_start, K))  # K is exclusive
            segment_start = K

    dsfunction = np.zeros((len(segment_bounds), 7))
    previous_price_high = pri0[0]
    for i, (start, end) in enumerate(segment_bounds):
        ncd_soc_vectors = numncd0[:, start:end].T
        unique_vectors, first_indices, counts = np.unique(
            ncd_soc_vectors, axis=0, return_index=True, return_counts=True
        )
        max_count = np.max(counts)
        mode_candidates = np.flatnonzero(counts == max_count)
        mode_index = mode_candidates[np.argmin(first_indices[mode_candidates])]

        dsfunction[i, 0] = previous_price_high
        dsfunction[i, 1] = pri0[end - 1]
        dsfunction[i, 2] = Res0_rounded[start]
        dsfunction[i, 3:7] = unique_vectors[mode_index]
        previous_price_high = pri0[end - 1]

    return dsfunction


def biddingNEW(N_t, N_price, eta,Cap,Smax,Smin,Pdmax,Pcmax,price,SOC_ini):
    # get prices
    pri = price[1:N_t]
    pri0 = np.linspace(min(price), max(price), N_price) # traverse price for the current interval from the minimum to maximum

    # initialize variables for the stair recognization
    iup= np.zeros((N_price)) # the index of interval when the battery first reach the maximum energy limit
    ilow= np.zeros((N_price)) # the index of interval when the battery first reach the minimum energy limit
    numncd0= np.zeros((4,N_price)) # three NCD counts plus the first-reached SOC-limit state (Smax=1, Smin=-1, neither=0)
    Tb= np.zeros((N_price)) # the index of interval when the battery first reach the maximum/minimum energy limit
    ResE= np.zeros((N_t,N_price)) # the energy of battery at each time interval
    Res= np.zeros((N_t,N_price)) # the power of battery at each time interval
    ResPd= np.zeros((N_t,N_price)) # the discharge power of battery at each time interval
    ResPc= np.zeros((N_t,N_price)) # the charge power of battery at each time interval
    Res0 = np.zeros((N_price)) # the power of battery at the current time interval

    # traverse the price for the current interval from the minimum to the maximum
    for K in range(N_price):
        # build the model
        model = gp.Model()
        # variable definition
        SOC = model.addVars( N_t+1, vtype=GRB.CONTINUOUS, lb=Smin, ub=Smax, name='SOC')
        Pc = model.addVars( N_t, vtype=GRB.CONTINUOUS, lb=0, ub=Pcmax, name='Pc')
        Pd = model.addVars( N_t, vtype=GRB.CONTINUOUS, lb=0, ub=Pdmax, name='Pd')
        # Constraints
        # SOC initialization
        model.addConstr((SOC[0] == SOC_ini), 'SOC_initial')
        # SOC update
        model.addConstrs((Cap*SOC[t] == Cap*SOC[t-1] - (Pd[t-1]/eta - eta*Pc[t-1])*(24/N_t) for t in range(1, N_t+1)), 'SOC')
        # SOC bounds
        model.addConstrs(((SOC[t] >= Smin) for t in range(1, N_t+1)), 'SOC_lower_bound')
        model.addConstrs(((SOC[t] <= Smax) for t in range(1, N_t+1)), 'SOC_upper_bound')
        # objective function
        model.setObjective(((pri0[K]*((Pd[0]-Pc[0]))*(24/N_t) +(sum(pri[t-1]*((Pd[t]-Pc[t])*(24/N_t)) for t in range(1,N_t)))) ), GRB.MAXIMIZE)
        # solve the model
        model.setParam('OutputFlag', 0)
        model.optimize()
        # get power at time 0
        Res0[K] = Pd[0].X-Pc[0].X # get the values of the power of battery at the current time interval
        # adjust the power for simultaneously charge and discharge
        if Pd[0].X*Pc[0].X !=0:
            if Pd[0].X>Pc[0].X*eta**2:
                Res0[K] = Pd[0].X-Pc[0].X*eta**2
            else:
                Res0[K] = Pd[0].X/(eta**2)-Pc[0].X

        # stair recognization
        for t in range(N_t):
            if Pd[t].X*Pc[t].X !=0: # adjust the power for simultaneously charge and discharge
                if Pd[t].X>Pc[t].X*eta**2:
                    Res[t,K] = Pd[t].X-Pc[t].X*eta**2
                    ResPd[t,K] = Pd[t].X-Pc[t].X*eta**2
                    ResPc[t,K] = 0
                else:
                    Res[t,K] = Pd[t].X/(eta**2)-Pc[t].X
                    ResPd[t,K] = 0
                    ResPc[t,K] = Pd[t].X/(eta**2)-Pc[t].X
            else:
                if Pd[t].X>Pc[t].X:
                    ResPd[t,K] = Pd[t].X
                    ResPc[t,K] = 0
                    Res[t,K] = Pd[t].X
                else:
                    ResPc[t,K] = -Pc[t].X
                    ResPd[t,K] = 0
                    Res[t,K] = -Pc[t].X
        for t in range(N_t-1):
            ResE[t,K] = SOC[t+1].X
        # the index of interval when the battery first reach the maximum energy limit
        upper_limit_hits = np.flatnonzero(np.isclose(ResE[:, K], Smax, rtol=0, atol=1e-6))
        if len(upper_limit_hits) == 0:
            iup[K] = 24
        else:
            iup[K] = upper_limit_hits[0] + 1
        # the index of interval when the battery first reach the minimum energy limit
        lower_limit_hits = np.flatnonzero(np.isclose(ResE[:, K], Smin, rtol=0, atol=1e-6))
        if len(lower_limit_hits) == 0:
            ilow[K] = 24
        else:
            ilow[K] = lower_limit_hits[0] + 1
        Tb[K]=min(iup[K],ilow[K]) # the index of interval when the battery first reach the maximum/minimum energy limit
        numncd0[0,K] = np.sum(ResPc[:int(Tb[K]),K]==-Pcmax) # the number of min-rate power before the battery first reach the maximum/mimium energy limit
        numncd0[1,K] = np.sum(ResPd[:int(Tb[K]),K]==Pdmax) # the number of max-rate power before the battery first reach the maximum/mimium energy limit
        numncd0[2,K] = np.sum((ResPc[:int(Tb[K]), K] + ResPd[:int(Tb[K]), K] >= -1e-3) &
                       (ResPc[:int(Tb[K]), K] + ResPd[:int(Tb[K]), K] <= 1e-3)) # the number of the null stair before the battery first reach the maximum/mimium energy limit
        if iup[K] < ilow[K]:
            numncd0[3,K] = 1
        elif ilow[K] < iup[K]:
            numncd0[3,K] = -1
        else:
            numncd0[3,K] = 0

    # Columns: price_low, price_high, powerstair, ncd0, ncd1, ncd2,
    # and first-reached SOC-limit state.
    dsfunction = build_dsfunction(pri0, Res0, numncd0)
    return [numncd0,dsfunction]


#%% calculate the profit of the battery that bid by M1
def calculate_profit(N_t, Nday, N_price, eta,CAP,Smax,Smin,Pdmax,Pcmax,price, Sinitial,meanstd, progress_desc):
    # Initialize the battery
    ESSOC_status = np.zeros((N_t*Nday))
    profit = np.zeros((N_t*Nday))
    P_cleared = np.zeros((N_t*Nday))
    ncd = np.zeros((N_t*Nday,4,N_price))
    dsfunctions = []

    # for each time interval bid a demand-supply function
    for t in tqdm(range(N_t*Nday), desc=progress_desc, unit='hour', leave=False):
        # generate prices with prediction error
        priceN_t = price[t:t+N_t] # get price of next N_t intervals
        noise = np.zeros((N_t)) # initialize noise
        priceN_t_with_error = np.zeros((N_t)) # initialize the priceN_t_with_error
        for tt in range(N_t):
            rate = meanstd / 100 + tt * 0.001 # relative standard deviation: 2% initially, +0.1% per hour
            noise[tt] = np.random.normal(0, rate) # generate Gaussian white noise for each time interval
            priceN_t_with_error[tt] = np.array(priceN_t[tt]*(1 + noise[tt])) # generate the prices with errors
            # adjust the extreme values
            if priceN_t_with_error[tt]<np.min(price):
                priceN_t_with_error[tt] =np.min(price)
            if priceN_t_with_error[tt]>np.max(price):
                priceN_t_with_error[tt]=np.max(price)

        # bid a demand-supply function using the current day's power bounds
        # (Pdmax/Pcmax are length-Nday arrays; fixed within a calendar day)
        d = t // N_t
        [numncd0,dsfunction] = biddingNEW(
            N_t, N_price, eta, CAP, Smax, Smin,
            Pdmax[d], Pcmax[d], priceN_t_with_error, Sinitial
        )
        dsfunctions.append(dsfunction.copy())

        # clearing and profit calculation
        price_cleared = priceN_t[0]
        for i in range(len(dsfunction)):
            price_low = dsfunction[i, 0]
            price_high = dsfunction[i, 1]
            if price_low <= price_cleared <= price_high: # the battery is cleared by regarded as a price-taker
                P_cleared[t] = dsfunction[i, 2]
        if P_cleared[t] >=0:
            Sinitial = Sinitial - P_cleared[t]/eta/CAP*(24/N_t)
        else:
            Sinitial = Sinitial - P_cleared[t]*eta/CAP*(24/N_t)
        ESSOC_status[t] = Sinitial
        profit[t] = price_cleared*P_cleared[t]*(24/N_t) # calculate the profit for each time interval
        ncd[t,:,:] = numncd0
    aveprice = np.mean(price[0:N_t*Nday])
    ESSend = ESSOC_status[-1]*CAP # the finally remained energy
    end_value = ESSend*aveprice # the profit of the finally remained energy
    return profit, ESSOC_status,P_cleared,end_value,ncd,dsfunctions

#%% calculate the profit of the battery that bid by M2 (the actual data)
def calculate_profit_actual(eta,CAP,price, N_t,Nday, netoutput,Sinitial):
    # Initialize the battery
    ESSOC_status = np.zeros((N_t*Nday))
    profit = np.zeros((N_t*Nday))
    # profit calculation
    for t in range(N_t*Nday):
        x = price[t]
        profit[t] = x*netoutput[t]*(24/N_t) # calculate the profit for each time interval
        if netoutput[t] >=0:
            Sinitial = Sinitial - netoutput[t]/eta/CAP*(24/N_t)
        else:
            Sinitial = Sinitial - netoutput[t]*eta/CAP*(24/N_t)
        ESSOC_status[t] = Sinitial
    aveprice = np.mean(price[0:N_t*Nday])
    ESSend = ESSOC_status[-1]*CAP # the finally remained energy
    end_value = ESSend*aveprice # the profit of the finally remained energy
    return profit, ESSOC_status,netoutput,end_value
#%% Generation cost and carbon emission

# calculate the generation cost and the carbon emission
def calculate_cost_and_carbon(gas_actual,curtailment, PESS_method,PESS_actual,N_t,Nday):

    gas_old = np.asarray(gas_actual, dtype=float)
    curtailment_old = np.asarray(curtailment, dtype=float)

    Pgas = np.zeros((N_t*Nday,3))
    Pgas[:,1] = gas_old
    Pgas[:,2] = np.maximum(gas_old+PESS_actual, 0.0) # without battery

    diff = PESS_method-PESS_actual # power discharged more than the actual case

    gas_new = gas_old.copy()
    curtailment_new = curtailment_old.copy()

    supply = diff > 0   # extra ESS supply reduces gas, then adds curtailment
    demand = diff < 0   # extra ESS demand absorbs curtailment, then adds gas

    gas_reduction = np.minimum(diff[supply], np.maximum(gas_old[supply], 0.0))
    gas_new[supply] = gas_old[supply] - gas_reduction
    curtailment_new[supply] = curtailment_old[supply] + (diff[supply] - gas_reduction)

    extra_demand = -diff[demand]
    curtailment_reduction = np.minimum(extra_demand, curtailment_old[demand])
    curtailment_new[demand] = curtailment_old[demand] - curtailment_reduction
    gas_new[demand] = gas_old[demand] + (extra_demand - curtailment_reduction)

    #gas_new = np.maximum(gas_new, 0.0)
    #curtailment_new = np.maximum(curtailment_new, 0.0)

    # renewable energy newly absorbed = curtailment reduction (negative = more curtailment)
    absorbed = curtailment_old - curtailment_new

    Pgas[:,0] = gas_new

    month_int = int(monthnum)
    Cost = np.column_stack([
        gas_cost(Pgas[:, 0], year, month_int, COST_MODE),
        gas_cost(Pgas[:, 1], year, month_int, COST_MODE),
        gas_cost(Pgas[:, 2], year, month_int, COST_MODE),
    ])
    # marginal ($/MWh) gas price at each gas output, consistent with COST_MODE
    MargPrice = np.column_stack([
        gas_marginal_price(Pgas[:, 0], year, month_int, COST_MODE),
        gas_marginal_price(Pgas[:, 1], year, month_int, COST_MODE),
        gas_marginal_price(Pgas[:, 2], year, month_int, COST_MODE),
    ])
    return Pgas, Cost, absorbed, diff, MargPrice

def calculate_main(meanstd,price, gas, battery, curtailment, Pdmax, Pcmax, Smax, Smin, Cap, eta, SINI):
    # V5: split the aggregate fleet into a controllable k-unit (optimised by the
    # bidding method) and a passive (1-k) remainder that keeps its actual output.
    # The controllable unit is a proportionally shrunk copy of the fleet, so its
    # capacity and power scale by k while its SOC trajectory (hence SINI) is
    # unchanged.  P_method = P_cleared_controlled + (1-k)*battery.

    # controllable k-unit: optimised bidding on the k-scaled battery
    profit_ctrl, ESSOC_status_ctrl, P_cleared_ctrl, end_value_ctrl, ncd, dsfunctions = calculate_profit(
        N_t, N_day, N_price, eta, Cap*k, Smax, Smin, Pdmax*k, Pcmax*k,
        price, SINI, meanstd, f'{year}-{monthnum}'
    )
    # passive (1-k) remainder: keeps its original actual output, (1-k)*battery.
    # At k=1 the remainder and its capacity are both zero; skip it to avoid a
    # 0/0 in the SOC update and let k=1 reproduce V3 exactly.
    battery_passive = (1 - k) * battery
    if (1 - k) > 0:
        profit_passive, ESSOC_status_passive, _, end_value_passive = calculate_profit_actual(
            eta, Cap*(1 - k), price, N_t, N_day, battery_passive, SINI
        )
    else:
        profit_passive = np.zeros((N_t*N_day))
        end_value_passive = 0.0
    # full actual baseline (100% of the fleet), unchanged from V3
    profit_actual, ESSOC_status_actual, P_cleared_actual, end_value_actual = calculate_profit_actual(
        eta, Cap, price, N_t, N_day, battery, SINI
    )

    # combined method output and profit
    P_cleared = P_cleared_ctrl + battery_passive
    profit = profit_ctrl + profit_passive
    total_profit = sum(profit_ctrl) + end_value_ctrl + sum(profit_passive) + end_value_passive
    total_profit_actual = sum(profit_actual) + end_value_actual

    # cost/carbon: full method output vs. full actual baseline
    Pgas, Cost, absorbed, diff, MargPrice = calculate_cost_and_carbon(gas,curtailment,P_cleared,P_cleared_actual,N_t,N_day)

    # summarize the results
    total_cost = sum(Cost[:,0])
    total_cost_actual = sum(Cost[:,1])
    total_cost_withoutESS = sum(Cost[:,2])
    res_date = {'price': price[:N_t*N_day], 'profit': profit, 'profit_actual': profit_actual,
    'P_ESS': P_cleared, 'P_ESS_controlled': P_cleared_ctrl, 'P_ESS_passive': battery_passive,
    'P_ESS_actual': P_cleared_actual,
    'P_natural_gas':Pgas[:,0],'P_natural_gas_actual':Pgas[:,1], 'P_renewable_absorbed':absorbed,
    'marginal_price_gas': MargPrice[:,0], 'marginal_price_gas_actual': MargPrice[:,1], 'marginal_price_gas_withoutESS': MargPrice[:,2],
    'cost': Cost[:,0], 'cost_actual': Cost[:,1], 'cost_withoutESS': Cost[:,2],
    'total_profit': total_profit, 'total_profit_actual': total_profit_actual,
    'total_cost': total_cost, 'total_cost_actual': total_cost_actual, 'total_cost_withoutESS': total_cost_withoutESS,
    'total_absorb': sum(absorbed)}

    output_dir = ensure_results_dir()
    df_results = pd.DataFrame(res_date)
    df_results.to_csv(
        output_dir / (
            f'{month}{year}_eta95%_std{int(meanstd)}_'
            f'{COST_MODE}_V5_k{int(k*100)}.csv'
        ),
        index=False,
    )
    return [total_profit,total_profit_actual, total_cost,total_cost_actual,total_cost_withoutESS,Cost,absorbed,P_cleared,P_cleared_ctrl,Pgas,ncd,dsfunctions]


#%% main

# Monthly total carbon (tonnes) used for the carbon-reduction rate, from
# CAISO-historical-co2-20260720.csv.  Values cover the full case-study period.
TOTALMONTH_CARBON = {
    '202301': 4271420.12,
    '202302': 3319377.50,
    '202303': 3161825.37,
    '202304': 2067690.22,
    '202305': 2816288.03,
    '202306': 2257595.84,
    '202307': 4423043.17,
    '202308': 5216585.65,
    '202309': 3828422.03,
    '202310': 4303965.85,
    '202311': 3973537.47,
    '202312': 4373438.85,
    '202401': 3858551.08,
    '202402': 3126751.18,
    '202403': 2261180.98,
    '202404': 1917601.57,
    '202405': 1649742.34,
    '202406': 2655335.59,
    '202407': 4669362.14,
    '202408': 4308940.88,
    '202409': 4077587.38,
    '202410': 4189340.87,
    '202411': 3620906.08,
    '202412': 4241056.99,
    '202501': 3655694.77,
    '202502': 2541019.37,
    '202503': 2337717.50,
    '202504': 1880394.26,
    '202505': 1962830.77,
    '202506': 2297500.97,
    '202507': 2716766.79,
    '202508': 3983855.56,
    '202509': 4226129.62,
    '202510': 3613190.63,
    '202511': 3704954.99,
    '202512': 4200458.41,
    '202601': 3539955.03,
    '202602': 2740413.26,
    '202603': 2700115.12,
    '202604': 1906090.45,
}


def run_one_month(num, export_dsfunctions=False):
    """Compute a single month (0-based index into monthlist) and return its
    summary dict.

    Sets the module-level globals the other functions read, writes the per-month
    CSV / .npy files, and returns the row that goes into the summary table.  This
    is the single source of truth for the per-month pipeline; both
    run_all_months and the distributed driver call it, so edits here apply to
    sequential and distributed modes alike.
    """
    global year, month, monthnum, N_day

    random.seed(42)
    np.random.seed(42)

    year_num, month_num = year_month_list[num]
    year = str(year_num)
    month = monthlist[num]
    monthnum = monthnumlist[num]
    N_day = calendar.monthrange(int(year), int(monthnum))[1]

    output_dir = ensure_results_dir()

    data = readdata(N_t * 12, N_t, N_day)
    price_glo, gas_glo, battery_glo, Pdmax_glo, Pcmax_glo, Smax_glo, Smin_glo, Cap_glo, eta_glo, SINI_glo, curtailment_glo, skipped_gas_dates = data
    result = calculate_main(
        meanstd, price_glo, gas_glo, battery_glo, curtailment_glo,
        Pdmax_glo, Pcmax_glo, Smax_glo, Smin_glo, Cap_glo, eta_glo, SINI_glo,
    )
    total_profit, total_profit_actual, total_cost, total_cost_actual, total_cost_withoutESS, costdetails, absorbed, P_cleared, P_cleared_ctrl, Pgas, ncd, dsfunctions = result

    np.save(output_dir / f'ncd_{month}{year}_{COST_MODE}_V5_k{int(k*100)}.npy', ncd)
    np.save(
        output_dir / f'Pcleared_{month}{year}_{COST_MODE}_V5_k{int(k*100)}.npy',
        P_cleared,
    )
    if export_dsfunctions:
        times = pd.date_range(
            f'{year}-{monthnum}-01 00:00:00', periods=len(dsfunctions), freq='h'
        )
        export_data = pd.DataFrame({
            'time': times,
            'dsfunction': [
                json.dumps(dsfunction.tolist(), separators=(',', ':'))
                for dsfunction in dsfunctions
            ],
            'P_ESS': P_cleared_ctrl,

        })
        export_data.to_excel(
            output_dir
            / f'dsfunction_{month}{year}_{COST_MODE}_V5_k{int(k*100)}.xlsx',
            sheet_name=f'{month}{year}',
            index=False,
        )

    totalabsorbed = float(np.sum(absorbed))
    totalgas = float(np.sum(gas_glo))
    rate_gas = totalabsorbed / totalgas
    carbon_reduce = (8500 / 1000) * 0.053165 * totalabsorbed
    year_month = f'{year}{monthnum}'
    totalmonth_carbon = TOTALMONTH_CARBON.get(year_month)
    rate_carbon = carbon_reduce / totalmonth_carbon if totalmonth_carbon is not None else np.nan
    return {
        'year_month': year_month,
        'k': k,
        'total_profit': total_profit,
        'total_profit_actual': total_profit_actual,
        'total_cost': total_cost,
        'total_cost_actual': total_cost_actual,
        'total_cost_withoutESS': total_cost_withoutESS,
        'total_absorbed': totalabsorbed,
        'total_natural_gas': totalgas,
        'rate_gas': rate_gas,
        'carbon_reduce': carbon_reduce,
        'rate_carbon': rate_carbon,
        'skipped_gas_dates': ','.join(skipped_gas_dates),
    }


def run_all_months():
    output_dir = ensure_results_dir()

    summaries = [
        run_one_month(num)
        for num in tqdm(range(len(monthlist)), desc='All months', unit='month')
    ]

    start_period = f'{START_YEAR_MONTH[0]}{START_YEAR_MONTH[1]:02d}'
    end_period = f'{END_YEAR_MONTH[0]}{END_YEAR_MONTH[1]:02d}'
    summary_path = (
        output_dir
        / f'summary_{start_period}_{end_period}_{COST_MODE}_V5_k{int(k*100)}.csv'
    )
    pd.DataFrame(summaries).to_csv(summary_path, index=False)
    return summary_path


def run_may_2025():
    """Run only May 2025 and export its NCD and hourly dsfunctions."""
    may_2025_index = year_month_list.index((2025, 5))
    summary = run_one_month(may_2025_index, export_dsfunctions=True)
    summary_path = (
        ensure_results_dir()
        / f'summary_202505_{COST_MODE}_V5_k{int(k*100)}.csv'
    )
    pd.DataFrame([summary]).to_csv(summary_path, index=False)
    return summary_path


if __name__ == '__main__':
    run_may_2025()
