Attributes {
    lat {
        String units "degrees_north";
    }
    lon {
        String units "degrees_east";
    }
    reftime {
        String units "Hour since 2022-09-06T06:00:00Z";
        String standard_name "forecast_reference_time";
        String long_name "GRIB reference time";
        String calendar "proleptic_gregorian";
    }
    time1 {
        String units "Hour since 2022-09-06T06:00:00Z";
        String standard_name "time";
        String long_name "GRIB forecast or observation time";
        String calendar "proleptic_gregorian";
    }
    height_above_ground3 {
        String units "m";
        String long_name "Specified height level above ground";
        String positive "up";
        Int32 Grib_level_type 103;
        String datum "ground";
    }
    height_above_ground2 {
        String units "m";
        String long_name "Specified height level above ground";
        String positive "up";
        Int32 Grib_level_type 103;
        String datum "ground";
    }
    Temperature_height_above_ground {
        String long_name "Temperature @ Specified height level above ground";
        String units "K";
        String abbreviation "TMP";
        Float32 missing_value NaN;
        String grid_mapping "LatLon_Projection";
        String coordinates "reftime time1 height_above_ground3 lat lon ";
        String Grib_Variable_Id "VAR_0-0-0_L103";
        Int32 Grib2_Parameter 0, 0, 0;
        String Grib2_Parameter_Discipline "Meteorological products";
        String Grib2_Parameter_Category "Temperature";
        String Grib2_Parameter_Name "Temperature";
        Int32 Grib2_Level_Type 103;
        String Grib2_Level_Desc "Specified height level above ground";
        String Grib2_Generating_Process_Type "Forecast";
        String Grib2_Statistical_Process_Type "UnknownStatType--1";
    }
    u-component_of_wind_height_above_ground {
        String long_name "u-component of wind @ Specified height level above ground";
        String units "m/s";
        String abbreviation "UGRD";
        Float32 missing_value NaN;
        String grid_mapping "LatLon_Projection";
        String coordinates "reftime time1 height_above_ground2 lat lon ";
        String Grib_Variable_Id "VAR_0-2-2_L103";
        Int32 Grib2_Parameter 0, 2, 2;
        String Grib2_Parameter_Discipline "Meteorological products";
        String Grib2_Parameter_Category "Momentum";
        String Grib2_Parameter_Name "u-component of wind";
        Int32 Grib2_Level_Type 103;
        String Grib2_Level_Desc "Specified height level above ground";
        String Grib2_Generating_Process_Type "Forecast";
        String Grib2_Statistical_Process_Type "UnknownStatType--1";
    }
    v-component_of_wind_height_above_ground {
        String long_name "v-component of wind @ Specified height level above ground";
        String units "m/s";
        String abbreviation "VGRD";
        Float32 missing_value NaN;
        String grid_mapping "LatLon_Projection";
        String coordinates "reftime time1 height_above_ground2 lat lon ";
        String Grib_Variable_Id "VAR_0-2-3_L103";
        Int32 Grib2_Parameter 0, 2, 3;
        String Grib2_Parameter_Discipline "Meteorological products";
        String Grib2_Parameter_Category "Momentum";
        String Grib2_Parameter_Name "v-component of wind";
        Int32 Grib2_Level_Type 103;
        String Grib2_Level_Desc "Specified height level above ground";
        String Grib2_Generating_Process_Type "Forecast";
        String Grib2_Statistical_Process_Type "UnknownStatType--1";
    }
    Precipitation_rate_surface {
        String long_name "Precipitation rate @ Ground or water surface";
        String units "kg.m-2.s-1";
        String abbreviation "PRATE";
        Float32 missing_value NaN;
        String grid_mapping "LatLon_Projection";
        String coordinates "reftime time1 lat lon ";
        String Grib_Variable_Id "VAR_0-1-7_L1";
        Int32 Grib2_Parameter 0, 1, 7;
        String Grib2_Parameter_Discipline "Meteorological products";
        String Grib2_Parameter_Category "Moisture";
        String Grib2_Parameter_Name "Precipitation rate";
        Int32 Grib2_Level_Type 1;
        String Grib2_Level_Desc "Ground or water surface";
        String Grib2_Generating_Process_Type "Forecast";
        String Grib2_Statistical_Process_Type "UnknownStatType--1";
    }
}
