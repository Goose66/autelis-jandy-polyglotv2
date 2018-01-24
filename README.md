# autelis-jandy-polyglotv2
A Nodeserver for Polyglot v2 that interfaces with the Autelis Pool Control (Jandy) device rvice to allow the ISY 994i to control Jandy/Zodiac Aqualink pool systems. See http://www.autelis.com/pool-control-for-jandy-zodiac.html for more information on the Autelis Pool Control device.

Instructions for Local (Co-resident with Polyglot) installation:

1. Copy the files from this repository to the folder ~/.polyglot/nodeservers/Autelis-Jandy in your Polyglot v2 installation.
2. Log into the Polyglot Version 2 Dashboard (https://(Polyglot IP address):3000)
3. Add the Autelis-Jandy nodeserver as a Local nodeserver type.
4. Add the following required Custom Configuration Parameters under Configuration:
```
    ipaddress - IP address of Autelis Pool Control device 
    username - login name for Autelis Pool Control device
    password - password for Autelis Pool Control device
```
5. Add the following optional Custom Configuration Parameters:
```
    pollinginterval - polling interval in seconds (defaults to 60)
    ignoresolar - ignore Solar Heat settings (defaults to False)
```
