# household battery dispatch

# Houshehold battery optimization scenario 

Most of the battery optimization code I found optimizes trading scenarios of commercial battery installations. Household battery optimization demands are different, so we try to develop a usable home battery optimization code.

The aim of the code is to optimize household battery usage taking leverage of the hourly tarriff. The idea of the optimization is to shift household energy consumption to cheapest hours.

**Scenario**

Household consumes power at constant rate of **hourly_consumption**, which can be satisfied from the grid or battery.

Battery can receive these commands:
- charge - battery is charged at variable rate (not exceeding maximum rate setting), the same time all household consumption is satisfied from the grid
- idle - battery stays idle, so all the household consumption is drawn from the grid 
- discharge - battery is satisfying household consumption by discharging, nothing is drawn from the grid 

Function takes as input:
- capacity of the battery,
- hourly price of the electricity in the grid for the known hours,
- titles of hours (e.g. Nordpool announces prices for tomorrow at 13.00 EET, so we use titles of hours not to lose track)
- current SOC and target SOC which has to be achieved at the end of hours,
- DOD, minimum depth of discharge
- full charge/discharge cycle costs **cost_of_cycle**,
- household's power demand per hour, 

Function returns a dict containing hour, command CMD (e.g. positive number means rate of charge in kw, 0 is idle, negative number is a discharge command) to manage behaviour of the battery, and projected SOC at end of this hour. 

Function provides aggregated calculations of the optimization results

The aim is to minimize price paid for electricity to the grid by the household. 

Usage example can be tested in Examples

# USAGE

# Example usage:
```
battery_capacity = 15  # kWh
hourly_prices = p
hour_number = h
initial_soc = 0.87  # %
final_soc= 0.5 # %
max_soc=1 # %
min_dod=0.2 # %
final_energy_value_per_kwh = 12 #  Estimate value of energy the first hour next day that helps us to decide what stateof charge should be left.
hourly_consumption = [1.6 for i in range(len(hourly_prices))]
cost_of_cycle_kwh = 5 # cents it costs per charge/discharge kwh (not cycle) of the battery (depreciacion costs)
max_allowed_grid_power = 22 # Max power privided by ESO
max_charge_power = 5 # maximum power in KW that battery can be charged with
max_discharge_power = 5 # maximum power in KW that battery can be discharged at
efficiency = 1

result = optimize_power_consumption(battery_capacity,
                                    hourly_prices,
                                    hour_number,
                                    initial_soc,
                                    final_soc,
                                    max_soc,
                                    min_dod,
                                    final_energy_value_per_kwh,
                                    hourly_consumption,
                                    efficiency,
                                    cost_of_cycle_kwh,
                                    max_allowed_grid_power,
                                    max_charge_power,
                                    max_discharge_power)


print("Report of commands")

title = ["HOUR", "PRICE", "CMD", "GRID", "SOC"]

print(f"{title[0]:>13} {title[1]:>8} {title[2]:>7} {title[3]:>7} {title[4]:>8}")
print(f"{initial_soc*battery_capacity:>47}")
for i in range(len(result['Hour'])):
  print(f"{result['Hour'][i]} | {result['HourlyPrices'][i]:6} | {result['CMD'][i]:>5} | {result['ImportFromGrid'][i]:5} | {result['Projected_SOC'][i]:>6}")

print()
print()
print("OPTIMIZATION RESULTS")
for k,v in result['OptimizationResult'].items():
  if isinstance(v, float):
    print(f"{k:>25}: {v:.2f}")
  else: print(f"{k:>25}: {v}")
```
