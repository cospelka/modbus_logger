#!/usr/bin/python3
# Copyright (C) 2023- Christian Ospelkaus
# This file is part of modbus_logger <https://github.com/cospelka/modbus_logger>.
#
# modbus_logger is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# modbus_logger is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with modbus_logger.  If not, see <http://www.gnu.org/licenses/>.

from pymodbus.client import ModbusTcpClient,ModbusUdpClient,ModbusSerialClient
from pymodbus.constants import Endian
from influxdb import InfluxDBClient
import math 
import time 
import datetime
import sys
import configparser
import json
import os
import argparse
import fcntl

# Parse ini file
# This dict will hold the modbus device classes
mdc={}
# This dict will hold the modbus connections
md={}
# This dict will hold the modbus devices
mc={}
iniparser = configparser.ConfigParser()
# This dict will hold the influxdb databases
influxdbs={}

iniparser.optionxform=str
iniparser.read('/usr/local/etc/modbus_logger.ini')
for section in iniparser.sections():
  [ sectiontype, name ] = section.split(" ",1)
  if sectiontype == "modbus_device_class":
    mdc[name]={}
    for key in iniparser[section]:
      try:
        mdc[name][key] = json.loads(iniparser[section][key])
      except Exception as e:
        print(e)
        print(f'JSON parse error in [{sectiontype} {name}], key {key}. Bye.')
        sys.exit(1)
      if mdc[name][key]["Typ"] == 1:
        mdc[name][key]["Dt"] = "bool"
  if sectiontype == "modbus_device":
    md[name]={}
    for key in iniparser[section]:
      try:
        md[name][key] = json.loads(iniparser[section][key])
      except Exception as e:
        print(e)
        print(f'JSON parse error in [{sectiontype} {name}], key {key}. Bye.')
        sys.exit(1)
  if sectiontype == "influxdb":
    influxdbs[name]={}
    for key in iniparser[section]:
      try:
        influxdbs[name][key] = json.loads(iniparser[section][key])
      except Exception as e:
        print(e)
        print(f'JSON parse error in [{sectiontype} {name}], key {key}. Bye.')
        sys.exit(1)
  if sectiontype == "modbus_connection":
    mc[name]={}
    for key in iniparser[section]:
      try:
        mc[name][key] = json.loads(iniparser[section][key])
      except Exception as e:
        print(e)
        print(f'JSON parse error in [{sectiontype} {name}], key {key}. Bye.')
        sys.exit(1)


# Appply some defaults
for name in mc:
  mc[name]["parity"]   = mc[name].get("parity","N")
  mc[name]["stopbits"] = mc[name].get("stopbits",1)
  mc[name]["bytesize"] = mc[name].get("bytesize",8)
  mc[name]["baudrate"] = mc[name].get("baudrate",38400)
  mc[name]["port"]     = mc[name].get("port",502)

for name in md:  
  md[name]["endian_byte"] = md[name].get("modbus_endian_byte","b")
  md[name]["endian_word"] = md[name].get("modbus_endian_word","b")

for name in mdc:  
  for var in mdc[name]:
    mdc[name][var]["Dt"]   = mdc[name][var].get("Dt","int16")
    mdc[name][var]["Mult"] = mdc[name][var].get("Mult",1)
    mdc[name][var]["Unit"] = mdc[name][var].get("Unit","")

# Abstand der Datenpunkte
interval=60.
# Wenn True, werden keine Daten nach influxdb gegeben und stattdessen erfolgt eine Konsolenausgabe
debug=False

# Länge des Ergebnissess (in words) als Funktion für die verschiedenen Datentypen
dl = { "uint32": 2,
       "int32":  2,
       "uint16": 1,
       "int16":  1,
       "uint8":  8,
       "int8":   8,
       "bool":   1
     }

def acquire_lock(label="default"):

    # Create empty lock file if it does not exist yet
    lock_file = "/root/.instance_" + label + ".lock"
    if not os.path.isfile(lock_file):
        with open(lock_file,"w") as f:
            f.write("")

    lock_file_pointer = os.open(lock_file, os.O_WRONLY)

    try:
        fcntl.lockf(lock_file_pointer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return(lock_file_pointer)
    except IOError:
        os.close(lock_file_pointer)
        return(None)

# Influx client starten
for db,dbvals in influxdbs.items():
  try:
    influxdbs[db]["influxconn"] = InfluxDBClient(**dbvals)
  except Exception as e:
    print(e)
    print(f'Could not setup InfluxDB client {db}. Bye.')
    sys.exit(1)

# Start modbus connections
for name in mc:
  if   mc[name]["type"] == "u":
    try:
      mc[name]["conn"] = ModbusUdpClient(mc[name]["host"],port=mc[name]["port"],timeout=1,retries=1)
    except Exception as e:
      print(e)
      print(f'Could not set up modbus udp connection {name} (Host {mc[name]["host"]}, Port {mc[name]["port"]}). Bye.')
      print(mc[name]["conn"])
      sys.exit(1)
  elif mc[name]["type"] == "t":
    try:
      mc[name]["conn"] = ModbusTcpClient(mc[name]["host"],port=mc[name]["port"],timeout=1,retries=1)
    except Exception as e:
      print(e)
      print(f'Could not set up modbus tcp connection {name} (Host {mc[name]["host"]}, Port {mc[name]["port"]}). Bye.')
      print(mc[name]["conn"])
      sys.exit(1)
  elif mc[name]["type"] == "r":
    try:
      mc[name]["conn"] = ModbusSerialClient(port     = mc[name]["serialdevice"],
                                            baudrate = mc[name]["baudrate"],
                                            bytesize = mc[name]["bytesize"],
                                            parity   = mc[name]["parity"],
                                            stopbits = mc[name]["stopbits"],
                                            timeout=1,
                                            retries=1
                                            )
    except Exception as e:
      print(e)
      print(f'Could not set up modbus rtu client {name} (Device {mc[name]["serialdevice"]} {mc[name]["baudrate"]} {mc[name]["bytesize"]}{mc[name]["parity"]}{mc[name]["stopbits"]}))...')
      print(mc[name]["conn"])
      sys.exit(1)

# Copy the modbus device class info and the modbus connection info and the influxdb info into the device info
for name in md:
  md[name]["vars"] = mdc[md[name]["class"]]
  md[name].update(mc[md[name]["conn"]])
  if "influxdb" in md[name]:
    md[name].update(influxdbs[md[name]["influxdb"]])
    
parser = argparse.ArgumentParser(usage="%(prog)s [OPTION]",description="Modbus datalogger.")
parser.add_argument( "--device",   help="device from ini file we should talk to" )
parser.add_argument( "--variable", help="variable we should set" )
parser.add_argument( "--value",    help="value to set" )
args = parser.parse_args()
if len(sys.argv) >= 2:
  if args.device and args.variable and args.value:
    if args.device in md:
      if "vars" in md[args.device]:
        if args.variable in md[args.device]["vars"]:
          lock_pointer = acquire_lock("modbus_logger")
          if not lock_pointer:
            print('Could not acquire lock.')
            sys.exit(1)
          md[args.device]["conn"].connect()
          if md[args.device]["vars"][args.variable]["Typ"] == 3:
            if "Mult" in md[args.device]["vars"][args.variable]:
              set_value=float(args.value)/md[args.device]["vars"][args.variable]["Mult"]
              set_value=int(set_value)
            mbargs = {
                      "address":  md[args.device]["vars"][args.variable]["Addr"],
                      "value":    set_value
                     }
            if "slaveid" in md[args.device]:
              mbargs["device_id"]=md[args.device]["slaveid"]
            print("Writing with argument")
            print(mbargs)
            md[args.device]["conn"].write_register(**mbargs) 
            md[args.device]["conn"].close()
          else:
            print(f'Known variable {args.variable} for known device {args.device} is not of type 3!')
            sys.exit(1)
          try:
            os.close(lock_pointer)
          except:
            print('Failed to close lock pointer.')
            sys.exit(1)
        else:
          print(f'Unknown variable {args.variable} for known device {args.device}')
          sys.exit(1)
      else:
        print(f'Known device {args.device} has no variables we can set.')
        sys.exit(1)
    else:
      print(f'Unknown device {args.device}')
      sys.exit(1)
    sys.exit(0)
  else:
    parser.print_help()
    sys.exit(1)

# 01.01.1970 - führt dazu, dass in der folgenden Schleife beim ersten Mal das Warten zu Anfang unterbleibt
laststart=datetime.datetime.fromtimestamp(0,tz=datetime.UTC)

while True:
  now=datetime.datetime.now(datetime.UTC)
  # Wie viel Zeit ist seit dem letzten Datenpunkt vergangen?
  td=now-laststart
  # Wie lange müssen wir noch warten, damit zwischen den Datenpunkten genau "interval" liegt?
  wt=interval-td.total_seconds()

  # Dann warten wir halt mal (außer beim ersten Durchgang, siehe oben)
  if wt > 0.:
    if debug:
      print(f'Sleeping for {wt:.1f} seconds.')
    time.sleep(wt)
  else:
    if debug:
      print('Proceeding with data taking right away.')

  # Zeitstempel des letzten Durchgangs merken
  laststart=datetime.datetime.now(datetime.UTC)

  for mdrun,mdrundat in md.items():
    # Lock 
    lock_pointer = acquire_lock("modbus_logger")
    if not lock_pointer:
      print('Could not acquire lock.')
      continue

    # Verbindung aufbauen. 
    try:
      mdrundat["conn"].connect()
    except Exception as e:
      print(e)
      print('Modbus connection failed.')
      try:
        os.close(lock_pointer)
      except:
        print('Failed to close lock pointer.')
      continue

    # Zeitstempel des Datenpunktes merken
    datatime=datetime.datetime.now(datetime.UTC)

    # Leerer dictionary für die Messergebnisse und deren Nachkommastellen (für floats). 
    meas={}
    decs={}

    # Hier halten wir fest, ob es irgendwelche Fehler gab (wird in Icinga gesteckt). 
    # checkres ist der übliche Nagios Plugin returncode (0: OK; 1: WARNING; 2: CRITICAL; 3: UNKNOWN)
    checkres=0
    checkstr=''
    perfstr=''

    # In diese Dateien wandert die Statusausgabe für Icinga. Wird dann von einem Icinga plugin ausgelesen. 
    statusfilename=f'/var/local/lib/modbus_logger/{mdrun}.txt'
    if debug:
      statusfilename=f'/var/local/lib/modbus_logger/{mdrun}_debug.txt'
    statusfilenametmp=statusfilename + '.tmp'
    # Jetzt geht es los mit dem Daten einlesen.
    # Schleife über alle einzulesenden Register
    for var,vardat in mdrundat["vars"].items(): 
      try:
        mbargs = { "address":  vardat["Addr"] }
        if "slaveid" in mdrundat:
          mbargs["slave"] = mdrundat["slaveid"]
        if vardat["Typ"] == 3 or vardat["Typ"] ==4:
          mbargs["count"]=dl[vardat["Dt"]]
        else:
          mbargs["count"] = 1

        if vardat["Typ"] == 1:
          result = mdrundat["conn"].read_coils(**mbargs)
          num_results = result.bits 
        elif vardat["Typ"] == 2:
          result = mdrundat["conn"].read_discrete_inputs(**mbargs)
          num_results = result.bits
        elif vardat["Typ"] == 3:
          result = mdrundat["conn"].read_holding_registers(**mbargs)
          num_results = result.registers
        elif vardat["Typ"] == 4:
          result = mdrundat["conn"].read_input_registers(**mbargs)
          num_results = result.registers
      except Exception as e:
        print(e)
        errmsg = f'Unable to read {mbargs["count"]} bytes from slave {mbargs.get("slaveid","N/A")}, address {mbargs["address"]} using function code {vardat["Typ"]}.'
        print(errmsg)
        checkres = 3
        checkstr += " " + errmsg
        break

      if not len(num_results) >= dl[vardat["Dt"]]:
        errmsg = f'Length {len(num_result)} of result does not correspond to expected length {dl[vardat["Dt"]]}.'
        print(errmsg)
        checkres=3
        checkstr += " " + errmsg
        break 
      if   vardat["Dt"] == "uint32":
        val = mdrundat["conn"].convert_from_registers(result.registers,data_type=mdrundat["conn"].DATATYPE.UINT32)
      elif vardat["Dt"] == "int32":
        val = mdrundat["conn"].convert_from_registers(result.registers,data_type=mdrundat["conn"].DATATYPE.INT32)
      elif vardat["Dt"] == "uint16":
        val = mdrundat["conn"].convert_from_registers(result.registers,data_type=mdrundat["conn"].DATATYPE.UINT16)
      elif vardat["Dt"] == "int16":
        val = mdrundat["conn"].convert_from_registers(result.registers,data_type=mdrundat["conn"].DATATYPE.INT16)
      elif vardat["Dt"] == "bool":
        val = num_results[0] 
      # Eigentlich sollten Bits ja in coils gespeichert werden. Dieser Code erlaubt es, auch einzelne Bits eines holding registers auszuwerten...
      if "Bits" in vardat:
        # Schleife über alle bekannten Bits im Holding Register
        for bit,bitdat in vardat["Bits"].items():
          bitval = ( val & ( 1 << bitdat["BitNum"] ) ) >> bitdat["BitNum"]
          # Man kann einen Standardwert vorgeben. Wenn der gemessene Wert abweicht, gibt es einen Alarm über Icinga. 
          if "Dflt" in bitdat:
            if bitval != bitdat["Dflt"]:
              if checkres<2:
                checkres=2
              checkstr += f' {bit}={bitval} hat nicht den Wert {bitdat["Dflt"]} ({bitdat["Comment"]})'
          # Hier merken wir uns den Wert, um am Ende alles zusammen in InfluxDB schreiben zu können. 
          meas[bit] = bitval
          if debug:
            print(f'{bit: <18} = {bitval:6}      ({bitdat["Comment"]})')
      else:
        # Das wäre jetzt der "normale" Fall. Hier müssen wir den Multiplier berücksichtigen, um eine ordentliche Gleitkommazahl zu produzieren. 
        decs[var] = round(math.log10(vardat["Mult"]))
        if decs[var] < 0:
          decs[var] = abs(decs[var])
        else:
          decs[var] = 0
        if decs[var] != 0:
          val = val*vardat["Mult"]
        unit = vardat.get("Unit","")

        # Man kann einen Standardwert vorgeben. Wenn der gemessene Wert abweicht, gibt es einen Alarm über Icinga. 
        if "Dflt" in vardat:
          if vardat["Dflt"] != val:
            if checkres<2:
              checkres=2
            checkstr += f' {var}={val} hat nicht den Wert {vardat["Dflt"]} ({vardat["Comment"]})'
        # Man kann auch Grenzwerte festlegen. Wenn die Latte gerissen wird, gibt es einen Alarm über Icinga. 
        # Weil ich faul bin, sind hier erst einmal nur obere Grenzen implementiert. 
        # Es gibt "WARNING" (checkres=1) und "CRITICAL" (checkres=2) Meldungen, wie üblich in Icinga / Nagios.
        if "maxcrit" in vardat:
          if val > vardat["maxcrit"]:
            if checkres<2:
              checkres=2
            checkstr += f' {var}={val:.{decs[var]}f} {unit} > {vardat["maxcrit"]} {unit}'
          elif val > vardat["maxwarn"]:
            if checkres<1:
              checkres=1
            checkstr += f' {var}={val:.{decs[var]}f} {unit} > {vardat["maxwarn"]} {unit}'
        # Hier merken wir uns den Wert, um am Ende alles zusammen in InfluxDB schreiben zu können. 
        meas[var]=val
        if debug:
          if vardat["Typ"] == 1 or vardat["Typ"] == 2:
            print(f'{var: <18} = {val!s:^6}({vardat["Comment"]})')
          else:
            print(f'{var: <18} = {val:6.{decs[var]}f} {vardat["Unit"]: <4} ({vardat["Comment"]})')
    try:
      mdrundat["conn"].close()
    except Exception as e:
      print(e)
      print('Modbus connection failed to close.')
    try:
      os.close(lock_pointer)
    except Exception as e:
      print(e)
      print('Lock pointer failed to close.')
  
    # Wenn Daten da sind und wir nicht im Debug Modus sind und wenn kein Kommunikationsfehler auftrat, werden die Daten in InfluxDB geschrieben. 
    if not debug:
      if "influxdb" in mdrundat:
        if meas and checkres != 3:
          series = [
            {
              "measurement": mdrun,
              "tags":        {},
              "time":        datatime,
              "fields":      meas
            }
          ]
          try:
            mdrundat["influxconn"].write_points(series)
          except Exception as e:
            print(e)
            print('Could not write to InfluxDB')
        else:
          print('Not writing to InfluxDB. Either no data or data invalid.')
    else:
      print('Debug mode. Not attempting influxdb write.')
    try:
      mdrundat["conn"].close()
    except Exception as e:
      print(e)
      print('Could not close modbus connection')

    # Statusfile für Icinga geht auf die Platte. 
    try:
      with open(statusfilenametmp,'w') as f:
        for namerun,valrun in meas.items():
          perfstr += f"'{namerun}'="
          if namerun in decs:
            perfstr += f'{valrun:.{decs[namerun]}f}'
          else:
            perfstr += f'{valrun}'
          perfstr += ";U:U;U:U;U;U "
        f.write(f'{checkres}\n')
        checkstr = f' {mdrundat["comment"]}{checkstr}'
        f.write(f'{checkstr} |{perfstr}')
        f.close()
    except Exception as e:
      print('Could not write to tmp Icinga status file')
      print(e)
    try:
      os.replace(statusfilenametmp,statusfilename)
    except Exception as e:
      print('Could not write to Icinga status file.')
      print(e)

    time.sleep(1)

