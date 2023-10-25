"# household battery dispatch" 

# Houshehold battery optimization scenario to be implemented


Household consumes power at constant rate of **hourly_consumption**, which can be satisfied from the grid or battery.

Battery can receive these commands:
- charge - battery is charged at the maximum 5kwh rate, and all household consumption is satisfied from the grid
- idle - battery stays idle, so all the household consumption is drawn from the grid 
- discharge - battery is satisfying household consumption by discharging, nothing is drawn from the grid 

Function has to take as input capacity of the battery, hourly price of the electricity in the grid for the knwon hours, current SOC and target SOC which has to be achieved at the end of hours.

Function takes into account that full charge/discharge cycle costs **cost_of_cycle**, assumes constant power consumption of the household, asymetrical charge abd discharge **max_charge_power**. 

Function has to return a dict containing hour, command (e.g. charge/idle/discharge) to manage behaviour of the battery, and projected SOC at end of this hour. 

Calculate cost of electricity with and without optimization.

The aim is to minimize price paid for electricity to the grid by the household. 

