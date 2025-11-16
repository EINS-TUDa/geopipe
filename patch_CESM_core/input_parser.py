# -*- coding: utf-8 -*-
"""
Input Parsing Module

author: Sina Hajikazemi
date: 13.10.2023
"""
from pathlib import Path
import pandas as pd
import numpy as np
import scipy.interpolate
from collections import namedtuple
from core.params import Param_Index_Dict
import pkg_resources


class Parser:
    """
    Input Model Class
        Import techmap and timeseries
        Generates .dat input file
    """

    def __init__(self, name, techmap_dir_path, ts_dir_path, db_conn, scenario):
        self.techmap_path = techmap_dir_path.joinpath(f"{name}.xlsx")
        self.ts_dir_path = ts_dir_path
        
        self.scenario = scenario

        self.conn = db_conn
        self.cursor = self.conn.cursor()

        self.param_index_dict = Param_Index_Dict

        file_path = pkg_resources.resource_filename('cesm', 'core/init_queries.sql')
        # Read queries from the .sql file
        with open(file_path, 'r') as file:
            queries = file.read()

        # Execute the queries
        self.cursor.executescript(queries)

        # Commit the transaction
        self.conn.commit()
        self.units = None

    def parse(self):
        tmap = pd.ExcelFile(self.techmap_path)
        self.read_units(tmap)
        self.read_scenario(tmap)
        self.read_tss(tmap)
        self.read_co(tmap)
        self.read_cp(tmap)
        self.read_cs(tmap)

    def read_units (self, tmap) -> None:
        df = pd.read_excel(tmap,"Units")
        self.units = dict(zip(df["quantity"].str.strip(),df["scale_factor"]))
        # self.cursor.execute(f"INSERT INTO unit ({','.join(self.units.keys())}) VALUES (?,?,?,?,?,?,?);",list(self.units.values()))
        for _,row in df.iterrows():
            self.cursor.execute('''
                INSERT INTO unit (quantity, scale_factor, input, output)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(quantity) DO NOTHING
            ''', (row["quantity"], row["scale_factor"], row["input"], row["output"]))
        self.conn.commit()
    
    def _scale(self, p_name, value) -> float:
        if p_name in ("opex_cost_energy",):
            return value * self.units["cost_energy"]
        if p_name in ("opex_cost_power","capex_cost_power"):
            return value * self.units["cost_power"]
        if p_name in ("capex_cost_base",):
            return value * self.units["cost_power"]
        if p_name in ("spec_co2",):
            return value * self.units["co2_spec"]
        if p_name in ("max_eout", "min_eout"):
            return value * self.units["energy"]
        if p_name in ("cap_min", "cap_max", "cap_res_min", "cap_res_max", "cap_active"):
            return value * self.units["power"]
        if p_name in ("max_units",):
            return value
        else:
            return value
    
    def read_scenario(self, tmap):
        df = pd.read_excel(tmap,"Scenario")
        row_index = df.index[df["scenario_name"]==self.scenario].tolist()[0]
        # Get years set
        years = [x for x in 
            range(
                int(df["from_year"][row_index]),
                int(df["until_year"][row_index])+1,
                int(df["year_step"][row_index])
            )
        ]
        for y in years:
            self.cursor.execute("INSERT INTO year (value) VALUES (?);",(y,))

        discount_rate = df["discount_rate"][row_index]
        tss_name = df["TSS"][row_index]
        self.cursor.execute("INSERT INTO param_global (discount_rate, tss_name) VALUES (?, ?);",(discount_rate,tss_name))
        self.conn.commit() # commit here because it is needed for inserting co2 params
        
        params = {}
        for pp in ["annual_co2_limit", "co2_price"]:
            v = df[pp][row_index]
            params[pp] = {}
            if not pd.isna(v):
                if '[' in str(v):
                    int_fun = self.get_interpolation_f(v)
                    for y in years:
                        val = int_fun(y)
                        if not np.isnan(val):
                            params[pp][y] = float(val) # val is a zero dimensional np array so float is needed
                else:
                    for y in years:
                        params[pp][y] = float(v)
        for y in years:
            y_id = self.cursor.execute("SELECT id FROM year WHERE value =?",(y,)).fetchall()[0][0]
            co2_price = params["co2_price"].get(y,None)
            annual_co2_limit = params["annual_co2_limit"].get(y,None)
            if co2_price is not None or annual_co2_limit is not None:
                self.cursor.execute("INSERT INTO param_y (y_id, annual_co2_limit, co2_price) VALUES (?,?,?);",(y_id, annual_co2_limit, co2_price))
        self.conn.commit()
                          
    def read_tss(self,tmap):
        tss_name = self.cursor.execute("SELECT tss_name FROM param_global").fetchall()[0][0]
        df = pd.read_excel(tmap,"TSS")
        row_index = df.index[df["TSS_name"]==tss_name].tolist()[0]

        dt = int(df["dt"][row_index])
        
        tss_file_path = self.ts_dir_path.joinpath(f"{tss_name}.txt")

        with open(tss_file_path) as tss_file:
            tss_array = tss_file.read().split("\n")
        tss_values = [int(x) for x in tss_array if x != ""]
        for ts in tss_values:
            self.cursor.execute("INSERT INTO time_step (value) VALUES (?);",(ts,))
        w = (8760/len(tss_values))/dt
        self.cursor.execute("UPDATE param_global SET dt = ?, w = ? WHERE constant_column = 1;", (dt,w))
        self.conn.commit()

    def read_co(self, tmap) -> None:
        df = pd.read_excel(tmap,"Commodity")
        for _,row in df.iterrows():
            if not pd.isna(row["commodity_name"]):
                self.cursor.execute("INSERT INTO commodity (name,plot_order,plot_color) VALUES (?,?,?);",(row["commodity_name"].strip(),row["order"],row["color"]))
        self.conn.commit()

    def read_cp(self,tmap) -> None:
        df = pd.read_excel(tmap,"ConversionProcess")
        for _,row in df.iterrows():
            if not pd.isna(row["conversion_process_name"]):
                self.cursor.execute("INSERT INTO conversion_process (name, plot_order, plot_color) VALUES (?,?,?);",(row["conversion_process_name"].strip(),row["order"],row["color"]))
        self.conn.commit()
        
    def read_cs(self, tmap):
        df = pd.read_excel(tmap,"ConversionSubProcess",  skiprows=[1, 2])
        # TO DO - verify the scenario of the conversion process
        param_names= df.columns[df.columns.to_list().index("scenario")+1:].to_list()
        for _, row in df.iterrows():
            if not pd.isna(row["conversion_process_name"]):
                cin_id = self.cursor.execute("SELECT id FROM commodity WHERE name =?",(row["commodity_in"],)).fetchall()[0][0]
                cout_id = self.cursor.execute("SELECT id FROM commodity WHERE name =?",(row["commodity_out"],)).fetchall()[0][0]
                cp_id = self.cursor.execute("SELECT id FROM conversion_process WHERE name =?",(row["conversion_process_name"],)).fetchall()[0][0]
                self.cursor.execute("INSERT INTO conversion_subprocess (cp_id, cin_id, cout_id) VALUES (?,?,?);",(cp_id,cin_id,cout_id))
                self.conn.commit()
                cs_id = self.cursor.lastrowid
                # self.datasets["CS"].append(cs)
                for p_name in param_names:
                    p = row[p_name]
                    if not pd.isna(p):
                        if "T" not in self.param_index_dict[p_name]: # not time dependent
                            if "Y" in self.param_index_dict[p_name]:
                                years = self.cursor.execute("SELECT id,value FROM year").fetchall()
                                #see if interval
                                if '[' in str(p): # inveterval given
                                    f_int = self.get_interpolation_f(p)
                                    for y_id,y in years:
                                        val = f_int(y)
                                        if not np.isnan(val):                                        
                                            val = float(f_int(y))
                                            try:
                                                self.cursor.execute(f"INSERT INTO param_cs_y (cs_id, y_id, {p_name}) VALUES (?,?,?);",(cs_id,y_id,self._scale(p_name,val)))
                                            except:
                                                self.cursor.execute(f"UPDATE param_cs_y SET {p_name} = ? WHERE cs_id = ? AND y_id = ?;",(self._scale(p_name,val),cs_id,y_id))
                                else: # no interval given - constant
                                    for y_id,y in years:
                                        try:
                                            self.cursor.execute(f"INSERT INTO param_cs_y (cs_id, y_id, {p_name}) VALUES (?,?,?);",(cs_id,y_id,self._scale(p_name,p)))
                                        except:
                                            self.cursor.execute(f"UPDATE param_cs_y SET {p_name} = ? WHERE cs_id = ? AND y_id = ?;",(self._scale(p_name,p),cs_id,y_id))
                            else: # not dependent on the year
                                try:
                                    self.cursor.execute(f"INSERT INTO param_cs (cs_id, {p_name}) VALUES (?,?);",(cs_id,self._scale(p_name,p)))
                                except:
                                    self.cursor.execute(f"UPDATE param_cs SET {p_name} = ? WHERE cs_id = ?;",(self._scale(p_name,p),cs_id))
                        else: # time dependent
                            ts_file_path = self.ts_dir_path.joinpath(f"{p}.txt")
                            tss = self.cursor.execute("SELECT id,value FROM time_step").fetchall()
                            Row = namedtuple("Row", ["id", "value"])
                            tss = [Row(*row) for row in tss]
                            with open(ts_file_path) as ts_file:
                                ts = ts_file.read().split(" ")
                                ts = [float(x) for x in ts if x != ""]
                                ts.insert(0,0) # add to match index
                                ts = np.array(ts)
                                ts = ts[[x.value for x in tss]]
                                     
                            #Potentential normalization
                            if p_name in ["output_profile"]:
                                total = float(sum(ts))
                                if total:
                                    ts = ts / total
                                    remainder = 1.0
                                    for value in ts[:-1]:
                                        remainder -= float(value)
                                    ts[-1] = float(max(0.0, remainder))
                            # insert to database
                            for i,time_step in enumerate(tss):
                                try:
                                    self.cursor.execute(f"INSERT INTO param_cs_t (cs_id, t_id, {p_name}) VALUES (?,?,?);",(cs_id,time_step.id,self._scale(p_name,ts[i])))
                                except:
                                    self.cursor.execute(f"UPDATE param_cs_t {p_name} = ? WHERE cs_id=? AND t_id=?;",(self._scale(p_name,ts[i]),cs_id,time_step.id,))
        self.conn.commit()

    def get_interpolation_f(self, param):
        """
        Get the interpolation function for the pairs inputed in the techmap
        Pairs must be in the form: [ YYYY value ; YYYY value; ...]

        Parameters
        ----------
        param : string
           parameters to make the interpolate function.
           Must be in the form: [YYYY value ; YYYY value; ...]

        Returns
        -------
        int_f : function
            anomymus function to calculate the interpolation of a Year Y

        """
        yy = []
        vals = []
        #remove brackes
        param = param.split("[")[1].split("]")[0]
        points = param.split(";")
        for pair in points:
            pair = pair.split(" ")
            #remove aditional white spaces
            [year, val] = [x for x in pair if x != ""]
            if val != "NaN":
                yy.append(int(year))
                vals.append(float(val))
                
        if len(yy) > 1:
            f = scipy.interpolate.interp1d(yy, vals, bounds_error = False, fill_value = (vals[0], vals[-1]))
        else:
            f = lambda x: vals[0] if x == yy[0] else np.nan  
        return f

    @staticmethod
    def pre_check_scenarios(name, techmap_dir_path):
        tmap = pd.ExcelFile(techmap_dir_path.joinpath(f"{name}.xlsx"))
        df = pd.read_excel(tmap,"Scenario")
        return df["scenario_name"].to_list()

if __name__ == '__main__':
    techmap_dir_path = Path(".").joinpath("Data", "Techmap")
    ts_dir_path = Path(".").joinpath("Data", "TimeSeries")
    db_dir_path = Path(".").joinpath("Runs", "DEModel_V2-Base4twk")
    file_path = Path(".").joinpath("Runs", "DEModel_V2-Base4twk", "db.sqlite")
    if file_path.exists():
        # Delete the file using unlink()
        file_path.unlink()
        print("File deleted successfully:", file_path)
    else:
        print("File does not exist:", file_path)
    parser = Parser("DEModel_V2",techmap_dir_path=techmap_dir_path, ts_dir_path=ts_dir_path ,db_dir_path=db_dir_path ,scenario = "Base4twk")
    parser.parse()