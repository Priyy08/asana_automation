
import openpyxl
import io

file_path = r"c:\Users\Acer\Desktop\asana_pms\Infill New.xlsx"

def list_sections():
    print(f"Scanning {file_path} for sections...")
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        sheet = wb.active
        
        found_sections = []
        seen = set()
        
        for i, row in enumerate(sheet.iter_rows(), start=1):
            # Check Column A (Index 0)
            cell_A = row[0]
            val_A = str(cell_A.value).strip() if cell_A.value else ""
            
            # Use exact logic from main.py
            if val_A and cell_A.font and cell_A.font.b:
                 if val_A.lower() != "responsible":
                     if val_A not in seen:
                         found_sections.append(val_A)
                         seen.add(val_A)
                         print(f"Row {i}: Found Section -> {val_A}")
        
        with open("sections_list.txt", "w") as f:
            for s in found_sections:
                f.write(f"- {s}\n")
                print(f"- {s}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_sections()
