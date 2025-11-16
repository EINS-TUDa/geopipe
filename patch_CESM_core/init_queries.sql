-- Create the 'time' table
CREATE TABLE IF NOT EXISTS time_step (
    id INTEGER PRIMARY KEY,
    value INTEGER,
    CONSTRAINT value_unique UNIQUE (value)
);

-- Create the 'year' table
CREATE TABLE IF NOT EXISTS year (
    id INTEGER PRIMARY KEY,
    value INTEGER,
    CONSTRAINT value_unique UNIQUE (value)
);

-- Create the 'commodity' table
CREATE TABLE IF NOT EXISTS commodity (
    id INTEGER PRIMARY KEY,
    name TEXT,
    plot_color TEXT,
    plot_order INTEGER,
    CONSTRAINT name_unique UNIQUE (name)
);

-- Create the 'conversion_process' table
CREATE TABLE IF NOT EXISTS conversion_process (
    id INTEGER PRIMARY KEY,
    name TEXT,
    plot_color TEXT,
    plot_order INTEGER,
    CONSTRAINT name_unique UNIQUE (name)
);

-- Create the 'conversion_subprocess' table
CREATE TABLE IF NOT EXISTS conversion_subprocess (
    id INTEGER PRIMARY KEY,
    cp_id INTEGER,
    cin_id INTEGER,
    cout_id INTEGER,
    FOREIGN KEY (cp_id) REFERENCES conversion_process(id),
    FOREIGN KEY (cin_id) REFERENCES commodity(id),
    FOREIGN KEY (cout_id) REFERENCES commodity(id),
    CONSTRAINT cs_unique UNIQUE (cp_id,cin_id,cout_id)
);

-- Create the 'unit' table
CREATE TABLE IF NOT EXISTS unit (
    id INTEGER PRIMARY KEY,
    quantity TEXT,
    scale_factor FLOAT,
    input TEXT,
    output TEXT,
    CONSTRAINT unique_quantity UNIQUE (quantity)
);


-- Create the 'param' table
CREATE TABLE IF NOT EXISTS param_global (
    id INTEGER PRIMARY KEY,
    dt FLOAT,
    w FLOAT,
    discount_rate FLOAT,
    tss_name text,
    -- Add a column that holds a constant value
    constant_column INTEGER DEFAULT 1,
    CONSTRAINT only_one_row UNIQUE (constant_column)
);

-- Create the 'param_cs' table
CREATE TABLE IF NOT EXISTS param_cs (
    id INTEGER PRIMARY KEY,
    cs_id INTEGER,
    spec_co2 FLOAT,
    efficiency FLOAT,
    technical_lifetime INTEGER,
    technical_availability FLOAT,
    max_units FLOAT,
    c_rate FLOAT,
    efficiency_charge FLOAT,
    is_storage BOOLEAN,
    FOREIGN KEY (cs_id) REFERENCES conversion_subprocess(id),
    CONSTRAINT cs_unique UNIQUE (cs_id)
);

-- Create the 'param_y' table
CREATE TABLE IF NOT EXISTS param_y (
    id INTEGER PRIMARY KEY,
    y_id INTEGER,
    annual_co2_limit FLOAT,
    co2_price FLOAT,
    FOREIGN KEY (y_id) REFERENCES year(id),
    CONSTRAINT y_unique UNIQUE (y_id)
);

-- Create the 'param_cs_y' table
CREATE TABLE IF NOT EXISTS param_cs_y (
    id INTEGER PRIMARY KEY,
    cs_id INTEGER,
    y_id INTEGER,
    opex_cost_energy FLOAT,
    opex_cost_power FLOAT,
    capex_cost_power FLOAT,
    capex_cost_base FLOAT,
    cap_active FLOAT,
    max_eout FLOAT,
    min_eout FLOAT,
    cap_min FLOAT,
    cap_max FLOAT,
    cap_res_max FLOAT,
    cap_res_min FLOAT,
    out_frac_min FLOAT,
    out_frac_max FLOAT,
    in_frac_min FLOAT,
    in_frac_max FLOAT,
    FOREIGN KEY (cs_id) REFERENCES conversion_subprocess(id),
    FOREIGN KEY (y_id) REFERENCES year(id),
    CONSTRAINT cs_y_unique UNIQUE (cs_id, y_id)
);

-- Create the 'param_cs_t' table
CREATE TABLE IF NOT EXISTS param_cs_t (
    id INTEGER PRIMARY KEY,
    cs_id INTEGER,
    t_id INTEGER,
    availability_profile FLOAT,
    output_profile FLOAT,
    FOREIGN KEY (cs_id) REFERENCES conversion_subprocess(id),
    FOREIGN KEY (t_id) REFERENCES time(id),
    CONSTRAINT cs_t_unique UNIQUE (cs_id, t_id)
);

-- Output tables

-- Create the 'output_global' table
CREATE TABLE IF NOT EXISTS output_global (
    id INTEGER PRIMARY KEY,
    OPEX FLOAT,
    CAPEX FLOAT,
    TOTEX FLOAT,
    -- Add a column that holds a constant value
    constant_column INTEGER DEFAULT 1,
    CONSTRAINT only_one_row UNIQUE (constant_column)
);


-- Create the 'output_y' table
CREATE TABLE IF NOT EXISTS output_y (
    id INTEGER PRIMARY KEY,
    y_id INTEGER,
    total_annual_co2_emission FLOAT,
    FOREIGN KEY (y_id) REFERENCES year(id),
    CONSTRAINT y_unique UNIQUE (y_id)
);

-- Create the 'output_cs_y' table
CREATE TABLE IF NOT EXISTS output_cs_y (
    id INTEGER PRIMARY KEY,
    cs_id INTEGER,
    y_id INTEGER,
    cap_new FLOAT,
    cap_active FLOAT,
    cap_res FLOAT,
    eouttot FLOAT,
    eintot FLOAT,
    e_storage_level_max FLOAT,
    dis_salvage_value FLOAT, 
    FOREIGN KEY (cs_id) REFERENCES conversion_subprocess(id),
    FOREIGN KEY (y_id) REFERENCES year(id),
    CONSTRAINT cs_y_unique UNIQUE (cs_id, y_id)
);


-- Create the 'output_cs_y_t' table
CREATE TABLE IF NOT EXISTS output_cs_y_t (
    id INTEGER PRIMARY KEY,
    cs_id INTEGER,
    y_id INTEGER,
    t_id INTEGER,
    eouttime FLOAT,
    eintime FLOAT,
    pin FLOAT,
    pout FLOAT,
    e_storage_level FLOAT,
    FOREIGN KEY (cs_id) REFERENCES conversion_subprocess(id),
    FOREIGN KEY (y_id) REFERENCES year(id),
    FOREIGN KEY (t_id) REFERENCES time(id),
    CONSTRAINT cs_y_t_unique UNIQUE (cs_id, y_id, t_id)
);

-- Create the 'output_co_y_t' table
CREATE TABLE IF NOT EXISTS output_co_y_t (
    id INTEGER PRIMARY KEY,
    co_id INTEGER,
    y_id INTEGER,
    t_id INTEGER,
    enetgen FLOAT,
    enetcons FLOAT,
    FOREIGN KEY (co_id) REFERENCES commodity(id),
    FOREIGN KEY (y_id) REFERENCES year(id),
    FOREIGN KEY (t_id) REFERENCES time(id),
    CONSTRAINT co_y_t_unique UNIQUE (co_id, y_id, t_id)
);

