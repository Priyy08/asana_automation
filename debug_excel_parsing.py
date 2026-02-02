
import pandas as pd
import io

# Path to the file
file_path = r"c:\Users\Acer\Desktop\asana_pms\Infill New.xlsx"

def test_parsing():
    print(f"Testing parsing for {file_path}")
    
    try:
        # Read without header to process row by row
        df = pd.read_excel(file_path, header=None)
        
        current_section = "General" 
        col_map = {} 
        
        tasks_found = 0
        sections_found = 0
        
        print("\n--- Starting Row Scan ---")
        
        for index, row in df.iterrows():
            values = [str(x).strip() for x in row.values]
            
            # Check for Header Row
            if 'Task' in values and 'Triggering task' in values:
                for idx, val in enumerate(values):
                    if val == 'Task': col_map['Task'] = idx
                    if val == 'Triggering task': col_map['Triggering'] = idx
                    if val == 'days': col_map['days'] = idx
                print(f"Row {index}: DETECTED HEADER ROW. Map: {col_map}")
                continue 
                
            if not col_map: continue
            
            def get_val(key):
                idx = col_map.get(key)
                if idx is None or idx >= len(values): return ""
                val = values[idx]
                return "" if val.lower() == 'nan' else val
            
            task_name = get_val('Task')
            triggers_raw = get_val('Triggering')
            days_raw = get_val('days')
            
            if not task_name: continue
            
            # Identify Section vs Task
            is_section = False
            # Debug print for suspicious rows
            # print(f"Row {index}: Name='{task_name}' Days='{days_raw}' Trig='{triggers_raw}'")
            
            if not days_raw and not triggers_raw:
                is_section = True
            
            if is_section:
                current_section = task_name 
                sections_found += 1
                if sections_found < 20 or "Preliminary" in current_section or "Purchase" in current_section:
                     print(f"Row {index}: [SECTION DETECTED] => '{current_section}'")
                continue
                
            # It is a Task
            tasks_found += 1
            # print(f"Row {index}: [TASK] {task_name} (Section: {current_section})")
            
        print(f"\n--- Summary ---")
        print(f"Sections Found: {sections_found}")
        print(f"Tasks Found: {tasks_found}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_parsing()
