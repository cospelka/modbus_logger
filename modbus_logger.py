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
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian
from pymodbus.framer.rtu_framer import ModbusRtuFramer
from influxdb import InfluxDBClient
import math 
import time 
import datetime
import sys
import configparser
import json
import os

# String (b vs. l) für Byte bzw Word order in Konstante für pymodbus übersetzen
bo = { "b": Endian.Big,
       "l": Endian.Little
     }

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
  md[name]["slaveid"]     = md[name].get("slaveid",0)

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
      mc[name]["conn"] = ModbusSerialClient(method='rtu',
                                            port     = mc[name]["serialdevice"],
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
    
# 01.01.1970 - führt dazu, dass in der folgenden Schleife beim ersten Mal das Warten zu Anfang unterbleibt
laststart=datetime.datetime.fromtimestamp(0)

while True:
  now=datetime.datetime.utcnow()
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
  laststart=datetime.datetime.utcnow()

  for mdrun,mdrundat in md.items():
    # Verbindung aufbauen. 
    try:
      mdrundat["conn"].connect()
    except Exception as e:
      print(e)
      print('Modbus connection failed.')
      continue

    # Zeitstempel des Datenpunktes merken
    datatime=datetime.datetime.utcnow()

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
      statusfilename=f'/usr/local/var/lib/modbus_logger/{mdrun}_debug.txt'
    statusfilenametmp=statusfilename + '.tmp'
    # Jetzt geht es los mit dem Daten einlesen.
    # Schleife über alle einzulesenden Register
    for var,vardat in mdrundat["vars"].items(): 
      try:
        if vardat["Typ"] == 1:
          result = mdrundat["conn"].read_coils(vardat["Addr"],count=1,slave=mdrundat["slaveid"])
          num_results = result.bits 
        elif vardat["Typ"] == 2:
          result = mdrundat["conn"].read_discrete_inputs(vardat["Addr"],count=1,slave=mdrundat["slaveid"])
          num_results = result.bits
        elif vardat["Typ"] == 3:
          result = mdrundat["conn"].read_holding_registers(vardat["Addr"],count=dl[vardat["Dt"]],slave=mdrundat["slaveid"])
          num_results = result.registers
        elif vardat["Typ"] == 4:
          result = mdrundat["conn"].read_input_registers(vardat["Addr"],count=dl[vardat["Dt"]],slave=mdrundat["slaveid"])
          num_results = result.registers
      except Exception as e:
        print(result)
        print(e)
        errmsg = f'Unable to read {dl[vardat["Dt"]]} bytes from slave {mdrundat["slaveid"]}, address {vardat["Addr"]} using function code {vardat["Typ"]}.'
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
      decoder = BinaryPayloadDecoder.fromRegisters(num_results, byteorder=bo[mdrundat["endian_byte"]], wordorder=bo[mdrundat["endian_word"]])
      if   vardat["Dt"] == "uint32":
        val = decoder.decode_32bit_uint()
      elif vardat["Dt"] == "int32":
        val = decoder.decode_32bit_int()
      elif vardat["Dt"] == "uint16":
        val = decoder.decode_16bit_uint()
      elif vardat["Dt"] == "int16":
        val = decoder.decode_16bit_int()
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
      f=open(statusfilenametmp,'w')
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
      os.replace(statusfilenametmp,statusfilename)
    except:
      print('Could not write to Icinga status file.')

    time.sleep(1)

