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

# This dict will hold the modbus device classes
mdc={}

# This dict will hold the modbus connections
mc={}

# This dict will hold the influxdb databases
ic={}

# Acquire a lock so that other programs know we may be sending data
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
def get_first_register(this_mdc,regtype):
  first_addr=None
  first_name=None
  for run_name,run_details in this_mdc["vars"].items():
    run_addr=run_details["Addr"]
    # Look at only those registers where the type matches
    if run_details["Typ"] == regtype:
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
def get_next_register(this_mdc,regtype,addr,name):
  next_addr=None
  next_name=None
  for run_name,run_details in this_mdc["vars"].items():
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
def get_prev_register(this_mdc,regtype,addr,name):
  prev_addr=None
  prev_name=None
  for run_name,run_details in this_mdc["vars"].items():
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
def get_varinfo_by_address(this_mdc,regtype,addr,name):
  for run_name,run_details in this_mdc["vars"].items():
    run_addr = run_details["Addr"]
    if run_name == name and run_addr == addr:
      return(run_details)
  return (None)

# Assign continuous memory blocks of at most 125 word length for a device
def set_memory_blocks(this_mdc):
  this_mdc["memory_blocks"] = dict()
  for regtype in [1,2,3,4]:
    this_mdc["memory_blocks"][regtype] = []
    (run_addr,run_name) = get_first_register(this_mdc,regtype)
    run_details = get_varinfo_by_address(this_mdc,regtype,run_addr,run_name)
    start_addr = run_addr
    # i is the index of the memory block
    i = 0
    varlist = []
    while run_addr != None:
      varlist.append(run_name)
      run_details["memory_block_index"] = i
      run_details["memory_block_offset"] = run_addr - start_addr
      (next_addr,next_name) = get_next_register(this_mdc,regtype,run_addr,run_name)
      next_details = get_varinfo_by_address(this_mdc,regtype,next_addr,next_name)
      # Adding the next register would make this data block longer than 125 words.
      # Or this is the last register. 
      # In either case, close the current memory block.
      if next_addr == None or next_addr + dl[next_details["Dt"]] - start_addr >= 125:
        this_mdc["memory_blocks"][regtype].append({ "start_addr": start_addr, "length": run_addr + dl[run_details["Dt"]] - start_addr, "varlist": varlist })
        i = i + 1
        start_addr = next_addr
        varlist = []
      run_addr = next_addr
      run_name = next_name
      run_details = next_details

# This is the function that actually reads the data via modbus and stores it in the memory blocks
def read_data(this_md, checkres, checkstr):
  # Establish the modbus connection
  this_mc=this_md["mc"]
  this_mdc=this_md["mdc"]
  try:
    if   this_mc["type"] == "u":
      conn = ModbusUdpClient(this_mc["host"],port=this_mc["port"],timeout=1,retries=1)
    elif this_mc["type"] == "t":
      conn = ModbusTcpClient(this_mc["host"],port=this_mc["port"],timeout=1,retries=1)
    elif this_mc["type"] == "r":
      conn = ModbusSerialClient(port=this_mc["serialdevice"],baudrate=this_mc["baudrate"],bytesize = this_mc["bytesize"],parity=this_mc["parity"],stopbits=this_mc["stopbits"],timeout=1,retries=1)
  except Exception as e:
    conn = None
    print(f'Could not set up modbus connection.')
  try:
    conn.connect()
  except Exception as e:
    conn = None
    print('Modbus failed to connect.')

  for regtype in [1,2,3,4]:
    for memory_block in this_mdc["memory_blocks"][regtype]:
      loaded = False
      if debug:
        print(f'Reading {memory_block["length"]} words starting from address {memory_block["start_addr"]}.')
      mbargs = { "address": memory_block["start_addr"] }
      if "slaveid" in this_md:
        mbargs["slave"] = this_md["slaveid"]
      mbargs["count"] = memory_block["length"]
      try:
        if   regtype == 1:
          result = conn.read_coils(**mbargs)
        elif regtype == 2:
          result = conn.read_discrete_inputs(**mbargs)
        elif regtype == 3:
          result = conn.read_holding_registers(**mbargs)
        elif regtype == 4:
          result = conn.read_input_registers(**mbargs)
      except Exception as e:
        checkstr = checkstr + f' read_fail({regtype},{memory_block["start_addr"]},{memory_block["length"]})'
        checkres = 3
        for var in memory_block["varlist"]:
          this_mdc["vars"][var]["Val"] = None
        continue
      for var in memory_block["varlist"]:
        this_var = this_mdc["vars"][var]
        if   this_var["Dt"] == "uint32":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.UINT32,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "int32":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.INT32,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "uint16":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.UINT16,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "int16":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.INT16,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "uint8":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.UINT16,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "int8":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.INT16,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "float16":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.FLOAT16,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "float32":
          val = conn.convert_from_registers(result.registers[this_var["memory_block_offset"]:this_var["memory_block_offset"]+dl[this_var["Dt"]]],
                                            data_type=conn.DATATYPE.FLOAT32,word_order=this_mdc["word_order"])
        elif this_var["Dt"] == "bool":
          val = result.bits[this_var["memory_block_offset"]]
        if this_var["Dt"].startswith("uint") and this_var["BitNum"] != None:
          val = ( val & ( 1 << this_var["BitNum"] ) ) >> this_var["BitNum"]
        if this_var["Decs"] != 0:
          val = val*this_var["Mult"]

        # It is possible to set a default value. If the measured value deviates from that, we produce 
        # an alert through the nagios plugin.  
        if this_var["Dflt"] != None:
          if this_var["Dflt"] != val:
            if checkres<2:
              checkres=2
              checkstr += f' {var}={val} (should be {this_var["Dflt"]})'
        # It is possible to set limits. If those are exceeded, the nagios plugin will throw an alert.  
        # "WARNING" (checkres=1) and "CRITICAL" (checkres=2)
        if this_var["MaxCrit"] != None:
          if val > this_var["MaxCrit"]:
            if checkres<2:
              checkres=2
              checkstr += f' {var}={val:.{this_var["Decs"]}f} {this_var["Unit"]} > {this_var["MaxCrit"]} {this_var["Unit"]}'
          elif val > this_var["MaxWarn"]:
            if checkres<1:
              checkres=1
              checkstr += f' {var}={val:.{this_var["Decs"]}f} {this_var["Unit"]} > {this_var["MaxWarn"]} {this_var["Unit"]}'
        if this_var["MinCrit"] != None:
          if val < this_var["MinCrit"]:
            if checkres<2:
              checkres=2
              checkstr += f' {var}={val:.{this_var["Decs"]}f} {this_var["Unit"]} < {this_var["MinCrit"]} {this_var["Unit"]}'
          elif val < this_var["MinWarn"]:
            if checkres<1:
              checkres=1
              checkstr += f' {var}={val:.{this_var["Decs"]}f} {this_var["Unit"]} < {this_var["MinWarn"]} {this_var["Unit"]}'
        this_var["Val"] = val
        if debug:
          print(f'  {var} {val}')
  if conn != None:
    conn.close()
  return(checkres, checkstr)

# The main loop
def modbus_logger():

  iniparser = configparser.ConfigParser()
  iniparser.optionxform=str
  iniparser.read('/usr/local/etc/modbus_logger.ini')
  for section in iniparser.sections():
    section_head = section.split(" ")
    section_type = section_head[0]
    section_name = section_head[1]
    section_options = section_head[2:]
    if section_type == "modbus_device_class":
      mdc[section_name]={}
      mdc[section_name]["vars"]={}
      if "word_order_little" in section_options:
        mdc[section_name]["word_order"] = "little"
      else:
        mdc[section_name]["word_order"] = "big"
      for key in iniparser[section]:
        try:
          mdc[section_name]["vars"][key] = json.loads(iniparser[section][key])
        except Exception as e:
          print(e)
          print(f'JSON parse error in [{section_type} {section_name}], key {key}. Bye.')
          sys.exit(1)
        if mdc[section_name]["vars"][key]["Typ"] == 1 or mdc[section_name]["vars"][key]["Typ"] == 2:
          mdc[section_name]["vars"][key]["Dt"] = "bool"
        else:
          mdc[section_name]["vars"][key]["Dt"] = mdc[section_name]["vars"][key].get("Dt","int16")
        mdc[section_name]["vars"][key]["Mult"]    = mdc[section_name]["vars"][key].get("Mult",1)
        mdc[section_name]["vars"][key]["Unit"]    = mdc[section_name]["vars"][key].get("Unit","")
        mdc[section_name]["vars"][key]["Comment"] = mdc[section_name]["vars"][key].get("Comment",key)
        mdc[section_name]["vars"][key]["Dflt"]    = mdc[section_name]["vars"][key].get("Dflt",None)
        mdc[section_name]["vars"][key]["MaxCrit"] = mdc[section_name]["vars"][key].get("MaxCrit",None)
        mdc[section_name]["vars"][key]["MaxWarn"] = mdc[section_name]["vars"][key].get("MaxWarn",None)
        mdc[section_name]["vars"][key]["MinCrit"] = mdc[section_name]["vars"][key].get("MinCrit",None)
        mdc[section_name]["vars"][key]["MinWarn"] = mdc[section_name]["vars"][key].get("MinWarn",None)
        mdc[section_name]["vars"][key]["BitNum"]  = mdc[section_name]["vars"][key].get("BitNum",None)
        mdc[section_name]["vars"][key]["Decs"]    = round(math.log10(mdc[section_name]["vars"][key]["Mult"]))
        if mdc[section_name]["vars"][key]["Decs"] < 0:
          mdc[section_name]["vars"][key]["Decs"] = abs(mdc[section_name]["vars"][key]["Decs"])
        else:
          mdc[section_name]["vars"][key]["Decs"] = 0
      set_memory_blocks(mdc[section_name])
    if section_type == "modbus_device":
      md[section_name]={}
      for key in iniparser[section]:
        try:
          md[section_name][key] = json.loads(iniparser[section][key])
        except Exception as e:
          print(e)
          print(f'JSON parse error in [{section_type} {section_name}], key {key}. Bye.')
          sys.exit(1)
    if section_type == "influxdb":
      ic[section_name]={}
      for key in iniparser[section]:
        try:
          ic[section_name][key] = json.loads(iniparser[section][key])
        except Exception as e:
          print(e)
          print(f'JSON parse error in [{section_type} {section_name}], key {key}. Bye.')
          sys.exit(1)
    if section_type == "modbus_connection":
      mc[section_name]={}
      for key in iniparser[section]:
        try:
          mc[section_name][key] = json.loads(iniparser[section][key])
        except Exception as e:
          print(e)
          print(f'JSON parse error in [{section_type} {section_name}], key {key}. Bye.')
          sys.exit(1)

  # Set up modbus connections
  for this_mc_name,this_mc in mc.items():
    this_mc["parity"]   = this_mc.get("parity","N")
    this_mc["stopbits"] = this_mc.get("stopbits",1)
    this_mc["bytesize"] = this_mc.get("bytesize",8)
    this_mc["baudrate"] = this_mc.get("baudrate",38400)
    this_mc["port"]     = this_mc.get("port",502)

  # Start influxdb clients
  for this_ic_name,this_ic in ic.items():
    try:
      this_ic["cono"] = InfluxDBClient(**this_ic)
    except Exception as e:
      print(e)
      print(f'Could not setup InfluxDB client {db}. Bye.')
      sys.exit(1)

  # Establish links from the modbus device to the modbus device class, modbus connection and influxdb connection.
  for this_md_name,this_md in md.items():
    this_md["mdc"] = mdc[this_md["class"]]
    this_md["mc"] = mc[this_md["conn"]]
    if "influxdb" in this_md:
      this_md["ic"] = ic[this_md["influxdb"]]

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
          for var,vardat in mdrundat["mdc"]["vars"].items():
            f.write(var.ljust(max(len(var),15)+1))
          f.write('\n')

      # This is the actual modbus data transfer
      lock_pointer = acquire_lock("modbus_logger_dev")
      if not lock_pointer:
        print('Could not acquire lock.')
        continue
      if debug:
        print('')
        print(f'+++++++ READING {mdrun}')
      (checkres, checkstr) = read_data(mdrundat, checkres, checkstr)
      try:
        os.close(lock_pointer)
      except Exception as e:
        print(e)
        print('Lock pointer failed to close.')
 
      # Loop over results
      with open(tablefilename,'a') as f:
        outstr=str(now).ljust(40)
        for (var,vardat) in mdrundat["mdc"]["vars"].items():
          # ( meas_tmp, decs_tmp, checkres, checkstr ) = get_value(mdrundat, var, checkres, checkstr)
          if vardat["Val"] != None:
            meas[var] = vardat["Val"]
            decs[var] = vardat["Decs"]
            if debug and False:
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
              mdrundat["ic"]["cono"].write_points(series)
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
      except Exception as e:
        print('Could not write to tmp Icinga status file')
        print(e)
      try:
        os.replace(statusfilenametmp,statusfilename)
      except Exception as e:
        print('Could not replace Icinga status file by tmp Icinga status file.')
        print(e)

      time.sleep(1)

if __name__ == "__main__":
  modbus_logger()
