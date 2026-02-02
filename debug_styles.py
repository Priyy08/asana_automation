
import openpyxl

file_path = r"c:\Users\Acer\Desktop\asana_pms\Infill New.xlsx"

def inspect_styles():
    print(f"Opening {file_path} with openpyxl...")
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheet = wb.active
        
        print("\n--- Inspecting First 20 Rows ---")
        for i, row in enumerate(sheet.iter_rows(min_row=1, max_row=20), start=1):
            # Assuming 'Task' is roughly column C (3)
            # But let's check all cells in row to find where data is.
            
            # Print basics
            vals = [c.value for c in row[:5]]
            print(f"Row {i} Values: {vals}")
            
            # Check style of column C (index 2)
            cell_c = row[2] 
            is_bold = cell_c.font.b if cell_c.font else False
            fill_color = cell_c.fill.start_color.index if cell_c.fill else "None"
            
            print(f"   => Col C Style: Bold={is_bold}, Fill={fill_color}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_styles()
