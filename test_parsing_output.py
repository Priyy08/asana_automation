
import openpyxl
import io
import os

file_path = r"c:\Users\Acer\Desktop\asana_pms\Infill New.xlsx"

def test_parsing_logic():
    print(f"Testing parsing on {file_path}")
    
    with open(file_path, "rb") as f:
        content = f.read()
        
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    sheet = wb.active
    
    tasks_data = []
    current_section = "General"
    col_map = {} 
    header_found = False
    
    print("\n--- Rows Scan ---")
    for i, row in enumerate(sheet.iter_rows(), start=1):
        values = [str(c.value).strip() if c.value is not None else "" for c in row]
        
        if not header_found:
            if 'Task' in values and 'Triggering task' in values:
                for idx, val in enumerate(values):
                    if val == 'Task': col_map['Task'] = idx
                    if val == 'Triggering task': col_map['Triggering'] = idx
                header_found = True
            continue

        if not col_map: continue
        
        task_cell = row[col_map['Task']]
        if not task_cell.value: continue
        
        task_name = str(task_cell.value).strip()
        if task_name in ['Task', 'Triggering task']: continue
        
        # Check BOLD
        is_bold = task_cell.font and task_cell.font.b
        
        if is_bold:
            current_section = task_name
            print(f"Row {i}: [SECTION] '{task_name}' (Bold={is_bold})")
            continue
            
        print(f"Row {i}: [TASK] '{task_name}' -> Section: '{current_section}' (Bold={is_bold})")

if __name__ == "__main__":
    test_parsing_logic()
