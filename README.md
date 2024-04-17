household battery dispatch

 # Houshehold battery optimization scenario 

 Most of the battery optimization code we found optimizes trading scenarios of commercial battery installations. Household battery optimization demands are different from industrial uses, so we try to develop a usable home battery optimization code.

 The aim of the code is to optimize household battery usage taking leverage of the hourly tarriff. The idea of the optimization is to shift household energy consumption shiftin loads from most expensive to cheapes hours.

 Furthermore, we add to the equation major energy consumers with their particular behaviors and constraints. e.g. electric vehicle, heat pump.

 Further we will add PV generation forecast into the equation.

 

 **Scenario**

 Household consumes power at rate of **hourly_consumption** (expressed as a list of values), which can be satisfied from the grid or battery.

 Battery can receive these commands:
 - charge - battery is charged at variable rate (not exceeding maximum rate setting), the same time all household consumption is satisfied from the 
grid
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

 Function returns a dict containing hour, command CMD (e.g. positive number means rate of charge in kw, 0 is idle, negative number is a discharge 
command) to manage behaviour of the battery, and projected SOC at end of this hour. 

 Function provides aggregated calculations of the optimization results

 The aim is to minimize price paid for electricity to the grid by the household. 

