#!/usr/bin/python3
# Polglot Node Server for Jandy/Zodia Aqualink through Autelis Pool Control Interface

import sys
import threading
import time

import autelisapi
import polyinterface

_ISY_BOOL_UOM = 2 # Used for reporting status values for Controller node
_ISY_INDEX_UOM = 25 # Index UOM for custom states (must match editor/NLS in profile):
_ISY_TEMP_F_UOM = 17 # UOM for temperatures (farenheit)
_ISY_TEMP_C_UOM = 4 # UOM for temperatures (celcius)
_ISY_THERMO_MODE_UOM = 67 # UOM for thermostat mode
_ISY_THERMO_HCS_UOM = 66 # UOM for thermostat heat/cool state
_ISY_VOLT_UOM = 72 # UOM for Voltage

_VBAT_CONST = 0.01464

_LOGGER = polyinterface.LOGGER

# Node class for equipment (pumps and aux relays)
class Equipment(polyinterface.Node):

    id = "EQUIPMENT"

    # Turn equipment ON - TCP connection monitoring will pick up status change
    def cmd_don(self, command):
        if self.controller.autelis.on(self.address):
            pass
        else:
            _LOGGER.warning("Call to Pool Controller in DON command handler failed for node %s.", self.address)

    # Turn equipment OFF - TCP connection monitoring will pick up status change
    def cmd_dof(self, command):
        if self.controller.autelis.off(self.address):
            pass
        else:
            _LOGGER.warning("Call to Pool Controller in DOF command handler failed for node %s.", self.address)

    # Run update function in parent before reporting driver values
    def query(self):
        self.controller.update_node_states(False)
        self.reportDrivers()

    drivers = [{"driver": "ST", "value": 0, "uom": _ISY_INDEX_UOM}]
    commands = {
        "DON": cmd_don,
        "DOF": cmd_dof
    }

# Node class for temperature controls (pool heat, spa heat, etc.)
class TempControl(polyinterface.Node):

    id = "TEMP_CONTROL"

    # Override init to handle temp units
    def __init__(self, controller, primary, address, name, tempUnit):
        self.set_temp_unit(tempUnit)
        super(TempControl, self).__init__(controller, primary, address, name)
        

    # Setup node_def_id and drivers for tempUnit
    def set_temp_unit(self, tempUnit):
        
        # set the id of the node for the ISY to use from the nodedef
        if tempUnit == "C":
            self.id = "TEMP_CONTROL_C"
        else:
            self.id = "TEMP_CONTROL"
            
        # update the drivers in the node
        for driver in self.drivers:
            if driver["driver"] in ("ST", "CLISPH", "CLISPC"):
                driver["uom"] = _ISY_TEMP_C_UOM if tempUnit == "C" else _ISY_TEMP_F_UOM

    # Enable heat - TCP connection monitoring will pick up status change
    def cmd_don(self, command):
        if self.controller.autelis.on(self.address):
            pass
        else:
            _LOGGER.warning("Call to Pool Controller in DON command handler failed for node %s.", self.address)

    # Disable heat - TCP connection monitoring will pick up status change
    def cmd_dof(self, command):
        if self.controller.autelis.off(self.address):
            pass
        else:
            _LOGGER.warning("Call to Pool Controller in DOF command handler failed for node %s.", self.address)

    # Set set point temperature - TCP connection monitoring will pick up status change
    def cmd_set_temp(self, command):
        
        value = int(command.get("value"))

        # determine setpoint element to change based on the node address
        if self.address == "poolht":
            name = "poolsp"
        elif self.address == "poolht2":
            name = "poolsp2"
        elif self.address == "spaht":
            name = "spasp"
        else:
            _LOGGER.warning("No setpoint for node %s - SET_TEMP command ignored.", self.address)
            return

        # set the setpoint element
        if self.controller.autelis.set_temp(name, value):
            pass
        else:
            _LOGGER.warning("Call to Pool Controller in SET_TEMP command handler failed for node %s.", self.address)

    # Set set point temperature - TCP connection monitoring will pick up status change
    def cmd_set_mode(self, command):
        
        value = int(command.get("value"))

        # determine model element to change based on the node address
        if value == 1: # Heat
            if self.controller.autelis.on(self.address):
                pass
            else:
                _LOGGER.warning("Call to Pool Controller in SET_MODE command handler failed for node %s.", self.address)
        else:
            if self.controller.autelis.off(self.address):
                pass
            else:
                _LOGGER.warning("Call to Pool Controller in SET_MODE command handler failed for node %s.", self.address)

    # Update the thermostat mode and HCS drivers from the state value from the Aqualink controller
    def update_mode_drivers(self, state, report=True):

        # state is 0 (Disabled)
        if state == "0":
            self.setDriver("CLIMD", 0, report)
            self.setDriver("CLIHCS", 0, report)

        # state is 1 (Enabled)
        elif state == "1":
            self.setDriver("CLIMD", 1, report)
            self.setDriver("CLIHCS", 0, report)

        # state is 2 (Heating)
        elif state == "2":
            self.setDriver("CLIMD", 1, report)
            self.setDriver("CLIHCS", 1, report)

    # Run update function in parent before reporting driver values
    def query(self):
        self.controller.update_node_states(False)
        self.reportDrivers()

    drivers = [
        {"driver": "ST", "value": 0, "uom": _ISY_TEMP_F_UOM},
        {"driver": "CLISPH", "value": 0, "uom": _ISY_TEMP_F_UOM},
        {"driver": "CLIMD", "value": 0, "uom": _ISY_THERMO_MODE_UOM},
        {"driver": "CLIHCS", "value": 0, "uom": _ISY_THERMO_HCS_UOM},
        {"driver": "CLISPC", "value": 0, "uom": _ISY_TEMP_F_UOM}
    ]
    commands = {
        "DON": cmd_don,
        "DOF": cmd_dof,
        "SET_MODE": cmd_set_mode,
        "SET_SPH": cmd_set_temp
    }

# Node class for controller
class Controller(polyinterface.Controller):

    id = "CONTROLLER"

    def __init__(self, poly):
        super(Controller, self).__init__(poly)
        self.name = "controller"
        self.autelis = None
        self.pollingInterval = 60
        self.ignoresolar = False
        self.lastPoll = 0
        self.currentTempUnit = "F"
        self.threadMonitor = None

    # Setup node_def_id and drivers for temp unit
    def set_temp_unit(self, tempUnit):
        
        # Update the drivers to the new temp unit
        for driver in self.drivers:
            if driver["driver"] == "CLITEMP":
                driver["uom"] = _ISY_TEMP_C_UOM if tempUnit == "C" else _ISY_TEMP_F_UOM

        # update the node definition in the Polyglot DB
        self.updateNode(self)

        self.currentTempUnit = tempUnit
       
    # change the temp units utilized by the nodeserver
    def change_temp_units(self, newTempUnit):
         
        # update the temp unit for the temp control nodes
        for addr in self.nodes:
            node = self.nodes[addr]
            if node.id in ("TEMP_CONTROL", "TEMP_CONTROL_C"):
               node.set_temp_unit(newTempUnit) 
               self.updateNode(node) # Calls ISY REST change command to change node_def_id
        
        # update the temp unit for the controller node
        self.set_temp_unit(newTempUnit)
        
    # Start the nodeserver
    def start(self):

        _LOGGER.info("Started Autelis Nodeserver...")

        # get controller information from custom parameters
        try:
            customParams = self.poly.config["customParams"]
            ip = customParams["ipaddress"]
            username = customParams["username"]
            password = customParams["password"]
        except KeyError:
            _LOGGER.error("Missing controller settings in configuration.")
            raise

        # get polling intervals and configuration settings from custom parameters
        try:
            self.pollingInterval = int(customParams["pollinginterval"])
        except (KeyError, ValueError):
            self.pollingInterval = 60
        try:
            self.ignoresolar = bool(customParams["ignoresolar"])
        except (KeyError, ValueError):
            self.ignoresolar = False

        # create a object for the autelis interface
        self.autelis = autelisapi.AutelisInterface(ip, username, password, _LOGGER)

        #  setup the nodes from the autelis pool controller
        self.discover_nodes() 
    
        # setup a thread for monitoring status updates from the Pool Controller
        self.threadMonitor = threading.Thread(target=autelisapi.status_listener, args=(ip, self.set_node_state, _LOGGER))
        self.threadMonitor.daemon = True
        self.threadMonitor.start()

    # called every long_poll seconds
    def longPoll(self):

        # check the monitor thread to see if it is still running
        if self.threadMonitor and not self.threadMonitor.is_alive():

            _LOGGER.warning("Status monitoring thread has terminated - restarting.")

            # Restart the monitor thread
            self.threadMonitor = threading.Thread(target=autelisapi.status_listener, args=(self.autelis.controllerAddr, self.set_node_state, _LOGGER))
            self.threadMonitor.daemon = True
            self.threadMonitor.start()

    # called every short_poll seconds
    def shortPoll(self):

        # if node server is not setup yet, return
        if self.autelis is None:
            return

        currentTime = time.time()

        # check for elapsed polling interval
        if (currentTime - self.lastPoll) >= self.pollingInterval:

            # update the node states
            _LOGGER.debug("Updating node states in AuteliseNodeServer.shortPoll()...")
            self.update_node_states(True) # Update node states
            self.lastPoll = currentTime

    # Override query to report driver values and child driver values
    def query(self):

        # update all nodes - don't report
        self.parent.update_node_states(False)

        # report drivers of all nodes
        for addr in self.nodes:
            self.nodes[addr].reportDrivers()

    # Create nodes for all devices from the autelis interface
    def discover_nodes(self):

        # get the status XML from the autelis device
        statusXML = self.autelis.get_status()

        if statusXML is None:
            _LOGGER.error("No status XML returned from Autelis device on startup.")
            sys.exit("Failure on intial communications with Autelis device.")   

        else:

            # Get the temp units and update the controller node if needed
            temp = statusXML.find("temp")
            tempUnit = temp.find("tempunits").text
            if tempUnit != self.currentTempUnit: # If not "F"              
                self.set_temp_unit(tempUnit)
 
            # Iterate equipment child elements and process each
            equipment = statusXML.find("equipment")
            for element in list(equipment):

                # Only process elements that have text values (assuming blank
                # elements are not part of the installed/configured equipment).
                # Also ignore solar heat if configuration flag is not set
                if not ((element.text is None) or (element.tag == "solarht" and self.ignoresolar)):

                    addr = element.tag

                    # Process temp control elements
                    if addr in ("poolht", "poolht2", "spaht", "solarht"):

                        # Create the TEMP_CONTROL node with the correct temp units
                        tempNode = TempControl(self, self.address, addr, addr, tempUnit)
                        self.addNode(tempNode)

                    # Process others (pumps and aux relays)
                    else:

                        # Create the EQUIPMENT node
                        equipNode = Equipment(self, self.address, addr, addr)
                        self.addNode(equipNode)
                        
    # Creates or updates the state values of all nodes from the autelis interface
    def update_node_states(self, report=True):

        # get the status XML from the autelis device
        statusXML = self.autelis.get_status()

        if statusXML is None:
            _LOGGER.warning("No XML returned from get_status().")
            self.setDriver("GV0", 0, report)

        else:

            # Parse status XML
            system = statusXML.find("system")
            equipment = statusXML.find("equipment")
            temp = statusXML.find("temp")

            # Check for change in temp units on device
            # Note: Should be picked up in TCP connection monitoring but just in case 
            tempUnit = temp.find("tempunits").text
            if tempUnit != self.currentTempUnit:
                self.change_temp_units(tempUnit)

            # Get the element values for the controller node
            runstate = int(system.find("runstate").text)
            opmode = int(system.find("opmode").text)
            lowbat = int(system.find("lowbat").text)
            vbat = float(system.find("vbat").text) * _VBAT_CONST
            airtemp = int(temp.find("airtemp").text)

            # Update the controller node drivers
            self.setDriver("GV0", runstate, report)
            self.setDriver("GV1", opmode, report)
            self.setDriver("GV2", lowbat, report)
            self.setDriver("BATLVL", vbat, report)
            self.setDriver("CLITEMP", airtemp, report)

            # Iterate equipment child elements and process each
            for element in list(equipment):

                addr = element.tag
                state = element.text

                # Process elements that have a corresponding node
                if addr in self.nodes:

                    node = self.nodes[addr]

                    # Process temp control elements
                    if addr in ("poolht", "poolht2", "spaht", "solarht"):

                        if addr == "poolht":
                            setPoint = int(temp.find("poolsp").text)
                            currentTemp = int(temp.find("pooltemp").text)
                        elif addr == "poolht2":
                            setPoint = int(temp.find("poolsp2").text)
                            currentTemp = int(temp.find("pooltemp").text)
                        elif addr == "spaht":
                            setPoint = int(temp.find("spasp").text)
                            currentTemp = int(temp.find("spatemp").text)
                        elif addr == "solarht":
                            setPoint = int(temp.find("poolsp").text)
                            currentTemp = int(temp.find("solartemp").text)

                        # Update node driver values
                        node.setDriver("ST", currentTemp, report)
                        node.setDriver("CLISPH", setPoint, report)
                        node.update_mode_drivers(state, report)

                    # Process others (pumps and aux relays)
                    else:

                        node.setDriver("ST", int(state), report)

    # Callback function for TCP connection monitoring thread
    def set_node_state(self, element, value):

        retVal = False

        # handle system and temp control elements specifically
        if element == "runstate":
            self.setDriver("GV0", int(value))
            retVal = True
        elif element == "model":
            retVal = True
        elif element == "dip":
            retVal = True
        elif element == "opmode":
            self.setDriver("GV1", int(value))
            retVal = True
        elif element == "vbat":
            self.setDriver("BATLVL", float(value) * _VBAT_CONST)
            retVal = True
        elif element == "lowbat":
            self.setDriver("GV2", int(value))
            retVal = True
        elif element == "poolsp":
            if "poolht" in self.nodes:
                self.nodes["poolht"].setDriver("CLISPH", int(value))
                retVal = True
        elif element == "poolsp2":
            if "poolht2" in self.nodes:
                self.nodes["poolht2"].setDriver("CLISPH", int(value))
                retVal = True
        elif element == "spasp":
            if "spaht" in self.nodes:
                self.nodes["spaht"].setDriver("CLISPH", int(value))
                retVal = True
        elif element == "pooltemp":
            if "poolht" in self.nodes:
                self.nodes["poolht"].setDriver("ST", int(value))
                retVal = True
            if "poolht2" in self.nodes:
                self.nodes["poolht2"].setDriver("ST", int(value))
                retVal = True
        elif element == "spatemp":
            if "spaht" in self.nodes:
                self.nodes["spaht"].setDriver("ST", int(value))
                retVal = True
        elif element == "airtemp":
            self.setDriver("CLITEMP", int(value))
            retVal = True
        elif element == "solartemp":
            if "solarht" in self.nodes:
                self.nodes["solarht"].setDriver("ST", int(value))
                retVal = True
        elif element == "tempunits": # Process temp unit change
            if self.currentTempUnit != value:
                self.change_temp_units(value)
            retVal = True
        elif element in ["poolht", "poolht2", "spaht", "solarht"]:
            if element in self.nodes:
                self.nodes[element].update_mode_drivers(value)
                retVal = True

        else:

            # update state for node with address of element tag
            if element in self.nodes:
                self.nodes[element].setDriver("ST", int(value))
                retVal = True

        return retVal

    drivers = [
        {"driver": "ST", "value": 0, "uom": _ISY_BOOL_UOM},
        {"driver": "GV0", "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "GV1", "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "GV2", "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "BATLVL", "value": 0, "uom": _ISY_VOLT_UOM},
        {"driver": "CLITEMP", "value": 0, "uom": _ISY_TEMP_F_UOM}
    ]
    commands = {"QUERY": query}

# Main function to establish Polyglot connection
if __name__ == "__main__":
    try:
        polyglot = polyinterface.Interface()
        polyglot.start()
        control = Controller(polyglot)
        control.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
