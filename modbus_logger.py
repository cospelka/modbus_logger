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
# import asyncio

# Debug mode
# If set to True, no influxdb logging will occur and you will see excessive console logging
debug = False

# Interval between two datapoints
interval=60.

# Result length (in words) for the different data types
dl = { "uint32":  2,
       "int32":   2,
       "uint16":  1,
       "float16": 1,
       "float32": 2,
       "int16":   1,
       "uint8":   1,
       "int8":    1,
       "bool":    1
     }

# This dict will hold the modbus devices
md={}

# Acquire a lock so that other programs no we may be sending data
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

# Get first register of given type
def get_first_register(mdname,regtype):
  global md
  global debug
  first_addr=None
  first_name=None
  if debug:
    print(f'get_first_register(): mdname={mdname} regtype={regtype}')
  for run_name,run_details in md[mdname]["vars"].items():
    run_addr=run_details["Addr"]
    # Look at only those registers where the type matches
    if run_details["Typ"] == regtype:
      if debug:
        print(f'get_first_register(): run_name={run_name} run_addr={run_addr} first_addr={first_addr}')
      if first_addr != None:
        # We already have a candidate based on previous iterations. 
        # Is the current item "lower"?
        if (run_addr < first_addr) or ((run_addr == first_addr) and (run_name < name)):
          first_addr = run_addr
          first_name = run_name
      else:
        # We do not have a candidate base on previous iterations. 
        # So this is our first candidate!
        first_addr = run_addr
        first_name = run_name
  return(first_addr,first_name)

# Get next register of given type
def get_next_register(mdname,regtype,addr,name):
  global md
  next_addr=None
  next_name=None
  for run_name,run_details in md[mdname]["vars"].items():
    run_addr=run_details["Addr"]
    # Look at only those registers where the type matches
    if run_details["Typ"] == regtype:
      # Current item is a candidate because either its address is higher than the 
      # input address or equal, but in the latter case the name is "higher" than the input name
      if (run_addr > addr) or ((run_addr == addr) and (run_name > name)):
        if next_addr != None:
          # We already have a candidate based on previous iterations. 
          # Is the current item "lower"?
          if (run_addr < next_addr) or ((run_addr == next_addr) and (run_name < next_name)):
            next_addr = run_addr
            next_name = run_name
        else:
          # We do not have a candidate base on previous iterations. 
          # So this is our first candidate!
          next_addr = run_addr
          next_name = run_name
  return([next_addr,next_name])

# Get previous register of given type
def get_prev_register(mdname,regtype,addr,name):
  global md
  prev_addr=None
  prev_name=None
  for run_name,run_details in md[mdname]["vars"].items():
    run_addr=run_details["Addr"]
    # Look at only those registers where the type matches
    if run_details["Typ"] == regtype:
      # Current item is a candidate because either its address is lower than the 
      # input address or equal, but in the latter case the name is "lower" than the input name
      if (run_addr < addr) or ((run_addr == addr) and (run_name < name)):
        if prev_addr != None:
          # We already have a candidate based on previous iterations. 
          # Is the current item "higer"?
          if (run_addr > prev_addr) or ((run_addr == prev_addr) and (run_name > prev_name)):
            prev_addr = run_addr
            prev_name = run_name
        else:
          # We do not have a candidate base on previous iterations. 
          # So this is our first candidate!
          prev_addr = run_addr
          prev_name = run_name
  return([prev_addr,prev_name])

# Get register info of given type by address
def get_varinfo_by_address(mdname,regtype,addr,name):
  global md
  for run_name,run_details in md[mdname]["vars"].items():
    run_addr = run_details["Addr"]
    if run_name == name and run_addr == addr:
      return(run_details)
  return (None)

# Assign continuous memory blocks of at most 125 word length for a device
def set_memory_blocks(mdname):
  global md
  md[mdname]["memory_blocks"] = dict()
  for regtype in [1,2,3,4]:
    md[mdname]["memory_blocks"][regtype] = []
    (run_addr,run_name) = get_first_register(mdname,regtype)
    run_details = get_varinfo_by_address(mdname,regtype,run_addr,run_name)
    start_addr = run_addr
    # i is the index of the memory block
    i = 0
    while run_addr != None:
      if debug:
        print(f'set_memory_blocks: {run_addr} {run_name}')
      run_details["memory_block"] = i
      run_details["memory_offset"] = run_addr - start_addr
      (next_addr,next_name) = get_next_register(mdname,regtype,run_addr,run_name)
      next_details = get_varinfo_by_address(mdname,regtype,next_addr,next_name)
      # Adding the next register would make this data block longer than 125 words.
      # Or this is the last register. 
      # In either case, close the current memory block.
      if next_addr == None or next_addr + dl[next_details["Dt"]] - start_addr >= 125:
        md[mdname]["memory_blocks"][regtype].append({ "start_addr": start_addr, "length": run_addr + dl[run_details["Dt"]] - start_addr, "data": None })
        i = i + 1
        start_addr = next_addr
      run_addr = next_addr
      run_name = next_name
      run_details = next_details

# This is the function that actually reads the data via modbus and stores it in the memory blocks
def read_data(mdname, checkres, checkstr):
  global md
  global debug

  for regtype in [1,2,3,4]:
    for memory_block in md[mdname]["memory_blocks"][regtype]:
      loaded = False
      if debug:
        print(f'Reading {memory_block["length"]} words starting from address {memory_block["start_addr"]}.')
      mbargs = { "address": memory_block["start_addr"] }
      if "slaveid" in md[mdname]:
        mbargs["slave"] = md[mdname]["slaveid"]
      mbargs["count"] = memory_block["length"]
      i = 0
      while not loaded and i < 5:
        try:
          if   regtype == 1:
            result = md[mdname]["conn"].read_coils(**mbargs)
            loaded = True
          elif regtype == 2:
            result = md[mdname]["conn"].read_discrete_inputs(**mbargs)
            loaded = True
          elif regtype == 3:
            result = md[mdname]["conn"].read_holding_registers(**mbargs)
            loaded = True
          elif regtype == 4:
            result = md[mdname]["conn"].read_input_registers(**mbargs)
            loaded = True
        except Exception as e:
          loaded = False
        if loaded:
          try:
            if   regtype == 1:
              memory_block["data"] = result.bits
              loaded = True
            elif regtype == 2:
              memory_block["data"] = result.bits
              loaded = True
            elif regtype == 3:
              memory_block["data"] = result.registers
              loaded = True
            elif regtype == 4:
              memory_block["data"] = result.registers
              loaded = True
          except Exception as e:
            memory_block["data"] = None
            loaded = False
        i += 1
      if not loaded:
        checkstr = checkstr + f' read_fail({regtype},{memory_block["start_addr"]},{memory_block["length"]})'
        checkres = 2
  return(checkres, checkstr)

# Get a specific value from the memory blocks
def get_value(mdname, name, checkres, checkstr):
  global md
  global debug
  if debug:
    print(f'get_value: mdname={mdname} name={name}')
  mult = md[mdname]["vars"][name]["Mult"]
  dflt = md[mdname]["vars"][name].get("Dflt",None)
  maxcrit = md[mdname]["vars"][name].get("maxcrit",None)
  maxwarn = md[mdname]["vars"][name].get("maxwarn",None)
  mincrit = md[mdname]["vars"][name].get("mincrit",None)
  minwarn = md[mdname]["vars"][name].get("minwarn",None)
  comment = md[mdname]["vars"][name].get("Comment","")
  regtype = md[mdname]["vars"][name]["Typ"]
  dt = md[mdname]["vars"][name]["Dt"]
  memory_block_index = md[mdname]["vars"][name]["memory_block"]
  memory_block_offset = md[mdname]["vars"][name]["memory_offset"]
  errorcode = 0
  errormsg = ""
  if md[mdname]["memory_blocks"][regtype][memory_block_index]["data"] != None:
    if   dt == "uint32":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.UINT32,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "int32":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.INT32,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "uint16":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.UINT16,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "int16":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.INT16,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "uint8":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.UINT16,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "int8":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.INT16,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "float16":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.FLOAT16,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "float32":
      val = md[mdname]["conn"].convert_from_registers(md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset:memory_block_offset+dl[dt]],
                                                      data_type=md[mdname]["conn"].DATATYPE.FLOAT32,
                                                      word_order=md[mdname]["endian_word"])
    elif dt == "bool":
      val = md[mdname]["memory_blocks"][regtype][memory_block_index]["data"][memory_block_offset]
    if dt.startswith("uint") and "BitNum" in md[mdname]["vars"][name]:
      val = ( val & ( 1 << md[mdname]["vars"][name]["BitNum"] ) ) >> md[mdname]["vars"][name]["BitNum"]
    decs = round(math.log10(mult))
    if decs < 0:
      decs = abs(decs)
    else:
      decs = 0
    if decs != 0:
      val = val*mult

    # It is possible to set a default value. If the measured value deviates from that, we produce 
    # an alert through the nagios plugin.  
    if dflt != None:
      if dflt != val:
        if checkres<2:
          checkres=2
          checkstr += f' {name}={val} (should be {dflt})'
    # It is possible to set limits. If those are exceeded, the nagios plugin will throw an alert.  
    # "WARNING" (checkres=1) and "CRITICAL" (checkres=2)
    if maxcrit != None:
      if val > maxcrit:
        if checkres<2:
          checkres=2
          checkstr += f' {name}={val:.{decs}f} {unit} > {maxcrit} {unit}'
      elif val > maxwarn:
        if checkres<1:
          checkres=1
          checkstr += f' {name}={val:.{decs}f} {unit} > {maxwarn} {unit}'
    if mincrit != None:
      if val < mincrit:
        if checkres<2:
          checkres=2
          checkstr += f' {name}={val:.{decs}f} {unit} < {mincrit} {unit}'
      elif val < minwarn:
        if checkres<1:
          checkres=1
          checkstr += f' {name}={val:.{decs}f} {unit} < {minwarn} {unit}'
    return (val, decs, checkres, checkstr)
  else:
    return (None, 0, checkres, checkstr)

# The main loop
def modbus_logger():

  global md
  global debug
  global interval

  # This dict will hold the modbus device classes
  mdc={}

  # This dict will hold the modbus connections
  mc={}

  # This dict will hold the influxdb databases
  influxdbs={}

  iniparser = configparser.ConfigParser()
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
    md[name]["endian_word"] = md[name].get("modbus_endian_word","big")

  for name in mdc:  
    for var in mdc[name]:
      mdc[name][var]["Dt"]      = mdc[name][var].get("Dt","int16")
      mdc[name][var]["Mult"]    = mdc[name][var].get("Mult",1)
      mdc[name][var]["Unit"]    = mdc[name][var].get("Unit","")
      mdc[name][var]["Comment"] = mdc[name][var].get("Comment",var)


  # Start influxdb client
  for db,dbvals in influxdbs.items():
    try:
      influxdbs[db]["influxconn"] = InfluxDBClient(**dbvals)
    except Exception as e:
      print(e)
      print(f'Could not setup InfluxDB client {db}. Bye.')
      sys.exit(1)

  # Set up modbus connections
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
                                              timeout  = 1,
                                              retries  = 1)
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

  for name in md:
    # Start modbus connections
    try:
      md[name]["conn"].connect()
    except Exception as e:
      print(e)
      print('Modbus connection failed.')
      sys.exit(1) 

  # This allows us to write a specific register and terminate
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

  # 01/01/1970 - Make sure we start immediately on the first run...
  laststart=datetime.datetime.fromtimestamp(0,tz=datetime.UTC)

  for mdrun,mdrundat in md.items():
    set_memory_blocks(mdrun)

  while True:
    now=datetime.datetime.now(datetime.UTC)

    # How much time has passed since the last run?
    td=now-laststart

    # How much longer do we need to wait until the difference between the two datapoints is exacly given by "interval"?
    wt=interval-td.total_seconds()

    # Wait for exactly that time. 
    if wt > 0.:
      if debug:
        print(f'Sleeping for {wt:.1f} seconds.')
      time.sleep(wt)
    else:
      if debug:
        print('Proceeding with data taking right away.')

    # When did the previous round start?
    laststart=datetime.datetime.now(datetime.UTC)

    for mdrun,mdrundat in md.items():

      # When did we start?
      datatime=datetime.datetime.now(datetime.UTC)

      # Empty dicts for measurement values and decimal place count 
      meas={}
      decs={}

      # These keep track of any errors. checkstr has the error message and checkres is a return code 
      # according to nagios plugin convention (0: OK; 1: WARNING; 2: CRITICAL; 3: UNKNOWN). 
      checkres=0
      checkstr=''
      perfstr=''

      # Status output file for nagios. Read by nagios plugin. 
      statusfilename=f'/var/local/lib/modbus_logger/{mdrun}.txt'
      if debug:
        statusfilename=f'/var/local/lib/modbus_logger/{mdrun}_debug.txt'
      statusfilenametmp=statusfilename + '.tmp'


      # A table-like file with all data
      tablefilename=f'/var/local/lib/modbus_logger/{mdrun}.tab'
      if not os.path.isfile(tablefilename):
        with open(tablefilename,'w') as f:
          f.write("time".ljust(40))
          for var,vardat in mdrundat["vars"].items():
            f.write(var.ljust(max(len(var),15)+1))
          f.write('\n')

      # This is the actual modbus data transfer
      lock_pointer = acquire_lock("modbus_logger_dev")
      if not lock_pointer:
        print('Could not acquire lock.')
        continue
      (checkres, checkstr) = read_data(mdrun, checkres, checkstr)
      try:
        os.close(lock_pointer)
      except Exception as e:
        print(e)
        print('Lock pointer failed to close.')
 
      # Loop over results
      with open(tablefilename,'a') as f:
        outstr=str(now).ljust(40)
        for (var,vardat) in mdrundat["vars"].items():
          ( meas_tmp, decs_tmp, checkres, checkstr ) = get_value(mdrun, var, checkres, checkstr)
          if meas_tmp != None:
            meas[var] = meas_tmp
            decs[var] = decs_tmp
            if debug:
              if vardat["Typ"] == 1 or vardat["Typ"] == 2:
                print(f'{var: <18} = {meas[var]!s:^6}({vardat["Comment"]})')
              else:
                print(f'{var: <18} = {meas[var]:6.{decs[var]}f} {vardat["Unit"]: <4} ({vardat["Comment"]})')
            # Output to table file
            outstr += (str(meas[var]) + " " + vardat["Unit"]).ljust(max(len(var),15)+1)
          else:
            outstr += "ERROR".ljust(max(len(var),15)+1)
  
        f.write(f'{outstr}\n')

      # If data is available and we are not in debug mode, write it to influxdb
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

      # Write nagios status file to disk 
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

if __name__ == "__main__":
  modbus_logger()
