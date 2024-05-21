household battery dispatch

 # Houshehold battery optimization

 Most of the battery optimization code we found optimizes trading scenarios of commercial battery installations. Household battery optimization demands are different from industrial uses, so we try to develop a usable home battery optimization code specifically targeting household usage.

 The code optimizes household battery usage leveraging hourly tarriff. Optimization shifts household energy consumption from most expensive to cheapes hours.

 The aim is to minimize price paid for electricity to the grid by the household. 

 To model household consumption patern as closely as possible, algorithm allows to add separately various constraints/energy devises of the household:

 - limit power of installed mains (this allows peak shaving scenarios),
 - define battery and its characteristics (capacity, max charge, max discharge, max soc, min soc, etc)
 - define fixed household consumption,
 - define heat_pump operations
 - define electrive vehicle operations

 These devices / constrains can be added separately, so they would reflect actual household configuration, and optimize this specific scenario.
 
 **Further plans**
 Further we will add PV generation forecast into optimization scenario.

 

 **Scenario**
optim.py code with comments is self explanatory, and test_optim.py demonstrates usage of the code.

**Output**
Output of optimization if for your customization to your household automation environment.


 
