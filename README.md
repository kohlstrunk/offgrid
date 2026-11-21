OFFGRID-Regulator

Prosa:
To operate an offgrid system (PV,Batteries,Heatpump,Generator/CHP) to supply a house, you are coming to the point, that volatile PV needs to be outregulated by the generator or heatpump to ensure a stable supply of heat and electricity. This software communicates with a wetter forecast service and some local sensors, predict the course of temperature and the battery state (SOC) using an optimal plan to operate the CHP and/or heatpump. 
Adjustable modeling parameters fits the software to different hardware setups. At the end no user interventions are needed anymore to run the system economically without downregulations of the PV production or power cuts. 

Prerequisites:
- an running Raspi-Zero with internet and serial connection to all offgrid components
- a bridge software that connects to the serial network on local port 1234
- internet connection to get the PV yield forecast of today and tomorrow

Stage of development:
- controls automatically a CHP generator and an heatpump (AC)
- get sensor data of the temperatures, battery SOC and generator status

For now this software was comletely written by ChatGPT. 