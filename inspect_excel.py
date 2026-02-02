
import pandas as pd
import json

file_path = r"c:\Users\Acer\Desktop\asana_pms\Infill New.xlsx"

try:
    # Read first 120 rows to capture the first two sections shown in screenshots
    df = pd.read_excel(file_path, header=None, nrows=120)
    
    # Print the first 10 rows and rows around 100-110 to see the transition
    print("--- HEAD (0-10) ---")
    print(df.iloc[0:10].to_string())
    print("\n--- MIDDLE (100-110) ---")
    print(df.iloc[100:110].to_string())
    
except Exception as e:
    print(e)
