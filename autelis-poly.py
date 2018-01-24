#!/usr/bin/python3
# Polglot Node Server for Jandy/Zodia Aqualink through Autelis Pool Control Interface

import sys
import threading
import time

import autelisapi
import polyinterface

_ISY_BOOL_UOM = 2 # Used for reporting status values for Controller node
_ISY_INDEX_UOM = 25 # Index UOM for custom states (must match editor/NLS in profile):
_ISY_TEMP_F_UOM = 17 # UOM for temperatures
_ISY_VOLT_UOM = 72

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

    # Run update function in parent before reporting driver values
    def query(self):
        self.controller.update_node_states(False)
        self.reportDrivers()

    drivers = [
        {"driver": "ST", "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "CLISPH", "value": 0, "uom": _ISY_TEMP_F_UOM},
        {"driver": "CLITEMP", "value": 0, "uom": _ISY_TEMP_F_UOM}
    ]
    commands = {
        "DON": cmd_don,
        "DOF": cmd_dof,
        "SET_TEMP": cmd_set_temp
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
        self.tempUnits = "F"
        self.threadMonitor = None

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

        # setup the nodes from the autelis pool controller
        self.update_node_states(True) # Report driver values
        self.lastPoll = time.time()

        # setup a thread for monitoring status updates from the Pool Controller
        self.threadMonitor = threading.Thread(target=autelisapi.status_listener, args=(ip, self.set_node_state, _LOGGER))
        self.threadMonitor.daemon = True
        self.threadMonitor.start()

    # called every long_poll seconds
    def longPoll(self):

        # check the monitor thread to see if it is still running
        if self.threadMonitor and not self.threadMonitor.is_alive():

            _LOGGER.warn("Status monitoring thread has terminated - restarting.")

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
        for node in self.nodes:
            self.nodes[node].reportDrivers()

        return True

    # Creates or updates the state values of all nodes from the autelis interface
    def update_node_states(self, report=True):

        # get the status XML from the autelis device
        statusXML = self.autelis.get_status()

        if statusXML is None:
            _LOGGER.warning("No XML returned from get_status().")

        else:

            # Parse status XML
            system = statusXML.find("system")
            equipment = statusXML.find("equipment")
            temp = statusXML.find("temp")

            # Get processing elements
            self.tempUnits = temp.find("tempunits").text

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

                # Only process elements that have text values (assuming blank
                # elements are not part of the installed/configured equipment).
                # Also ignore solar heat if configuration flag is not set
                if not ((element.text is None) or (element.tag == "solarht" and self.ignoresolar)):

                    addr = element.tag
                    state = int(element.text)

                    # Process temp control elements
                    if addr in ["poolht", "poolht2", "spaht", "solarht"]:

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

                        # Create the TEMP_CONTROL node if it doesn't exist
                        if addr in self.nodes:
                            tempNode = self.nodes[addr]
                        else:
                            tempNode = TempControl(self, self.address, addr, addr)
                            self.addNode(tempNode)

                        # Update node driver values
                        tempNode.setDriver("ST", state, report)
                        tempNode.setDriver("CLISPH", setPoint, report)
                        tempNode.setDriver("CLITEMP", currentTemp, report)

                    # Process others (pumps and aux relays)
                    else:

                        # Create the EQUIPMENT node if it doesn't exist
                        if addr in self.nodes:
                            equipNode = self.nodes[addr]
                        else:
                            equipNode = Equipment(self, self.address, addr, addr)
                            self.addNode(equipNode)

                        # Update node driver values
                        equipNode.setDriver("ST", state, report)

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
                self.nodes["poolht"].setDriver("CLITEMP", int(value))
                retVal = True
            if "poolht2" in self.nodes:
                self.nodes["poolht2"].setDriver("CLITEMP", int(value))
                retVal = True
        elif element == "spatemp":
            if "spaht" in self.nodes:
                self.nodes["spaht"].setDriver("CLITEMP", int(value))
                retVal = True
        elif element == "airtemp":
            self.setDriver("CLITEMP", int(value))
            retVal = True
        elif element == "solartemp":
            if "solarht" in self.nodes:
                self.nodes["solarht"].setDriver("CLITEMP", int(value))
                retVal = True
        elif element == "tempunits":
            self.tempUnits = value
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
