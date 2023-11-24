import pulp

def optimize_power_consumption(battery_capacity=15, # in kwh
                               hourly_prices=[],    # a list of values, expressed in cents
                               hour_number=[],      # numbers of hours
                               initial_soc=0.7,     
                               final_soc=0.5,       
                               max_soc=1,
                               min_dod=0.2,
                               final_energy_value_per_kwh=12, # in cents
                               hourly_consumption=[],         # list of values, kwh
                               efficiency=1,
                               cost_of_cycle_kwh=5,           # in cents
                               max_allowed_grid_power=10,     # kw
                               max_charge_power=5,            # kw
                               max_discharge_power=5):        # kw
    # set variables
    n = len(hourly_prices)
    hours = range(n)
    initial_soc = battery_capacity * initial_soc
    final_soc = battery_capacity * final_soc

    # Create a linear programming problem
    problem = pulp.LpProblem("Power_Optimization", pulp.LpMinimize)

    # Define decision variables
    charge_rate = pulp.LpVariable.dicts("ChargeRate", hours, lowBound = 0, upBound= max_charge_power)
    discharge_rate = pulp.LpVariable.dicts("DischargeRate", hours, lowBound = -max_discharge_power, upBound=0)

    electricity_import = pulp.LpVariable.dicts("import", hours, lowBound=0, upBound=battery_capacity)
    soc = pulp.LpVariable.dicts("SOC", hours, lowBound=0, upBound=battery_capacity)

    # Define the objective function to minimize cost
    electricity_cost = pulp.lpSum(electricity_import[hour] * hourly_prices[hour] for hour in hours)
    amortization_cost = pulp.lpSum( (-discharge_rate[hour]) * cost_of_cycle_kwh for hour in hours)
    remaining_value = soc[n - 1] * efficiency * final_energy_value_per_kwh
    daily_cost = electricity_cost + amortization_cost - remaining_value

    problem += daily_cost

    # Define constraints
    for hour in hours:
      if hour == 0: init_soc = initial_soc
      else: init_soc = soc[hour -1]
      problem += soc[hour] == init_soc + charge_rate[hour] + discharge_rate[hour] # Conservation of charge
      problem += soc[hour] <= battery_capacity # battery soc does not exceed capacity
      problem += soc[hour] >= battery_capacity * min_dod # battery depth of discharge should not go lower than that
      problem += soc[hour] <= battery_capacity * max_soc # battery soc should not go above that
      problem += soc[n-1] >= final_soc # SOC has to be no less than target at the end of cycle
      problem += electricity_import[hour] == (hourly_consumption[hour] + charge_rate[hour] + discharge_rate[hour] * efficiency)
      problem += electricity_import[hour] >= 0
      problem += electricity_import[hour] <= max_allowed_grid_power

    # Solve the linear programming problem
    status = problem.solve()

    # Extract the results of the solution
    result = {
        "Hour": [],
        "HourlyPrices": hourly_prices,
        "HourlyConsumption": hourly_consumption,
        "ChargeRate": [],
        "DischargeRate": [],
        "ImportFromGrid": [],
        "Projected_SOC": [],
        "Command": [],
        "CMD": [],
        "OptimizationResult": {}
    }

    for hour in hours:
      result["Hour"].append(hour_number[hour])
      result["ChargeRate"].append(charge_rate[hour].varValue)
      result["DischargeRate"].append(discharge_rate[hour].varValue)
      result["ImportFromGrid"].append(electricity_import[hour].varValue)
      result["Projected_SOC"].append(soc[hour].varValue)
      command = charge_rate[hour].varValue + discharge_rate[hour].varValue
      result["Command"].append("Charge" if command > 0 else "Discharge" if command < 0 else "Idle")
      result["CMD"].append(str(command))

    # report consolidated optimization results
    result["OptimizationResult"]["Status"] = pulp.LpStatus[status]
    result["OptimizationResult"]["Cost w/o optimization"] = sum([hourly_prices[i] * hourly_consumption[i] for i in hours])/100


    consumed_kwh = sum([hourly_consumption[i]
        for i in hours])
    fromGrid_kwh = sum([result['ImportFromGrid'][i]
        for i in hours])
    
    charged_kwh = sum([result['ChargeRate'][i]
        for i in hours])
    avg_imported_kwh_price = sum([result['ImportFromGrid'][i] * hourly_prices[i]
        for i in hours]) / fromGrid_kwh / 100
    diff_init_final_SOC = initial_soc - result["Projected_SOC"][n-1]

    result["OptimizationResult"]["Cost after optimization"] = ((sum([result['ImportFromGrid'][i] * hourly_prices[i]  -
          result['DischargeRate'][i] * cost_of_cycle_kwh
        for i in hours])) + diff_init_final_SOC * avg_imported_kwh_price) / 100

    result["OptimizationResult"]["Consumed kwh"] = consumed_kwh
    result["OptimizationResult"]["Consumed from grid kwh"] = fromGrid_kwh
    result["OptimizationResult"]["Charged kwh to battery"] = charged_kwh
    result["OptimizationResult"]["Price of residual kwh"] = avg_imported_kwh_price * 100 * efficiency

    return result
