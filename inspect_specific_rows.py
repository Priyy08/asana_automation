
import openpyxl
import io

file_path = r"c:\Users\Acer\Desktop\asana_pms\Infill New.xlsx"

def inspect_rows():
    wb = openpyxl.load_workbook(file_path, data_only=True)
    sheet = wb.active
    
    # We know from context:
    # Row 2: Preliminary planning (Section?)
    # Row 4: Project team pre-planning (Task?)
    # Note: openpyxl is 1-indexed.
    
    rows_to_check = [2, 4, 340] # 340 was detected as section previously
    
    with open("style_report.txt", "w") as f:
        for r_idx in rows_to_check:
            f.write(f"\n--- Checking Row {r_idx} ---\n")
            for c_idx in range(1, 7):
                cell = sheet.cell(row=r_idx, column=c_idx)
                val = cell.value
                font = cell.font
                fill = cell.fill
                
                f.write(f"  Col {c_idx}: Val='{val}'\n")
                if font:
                    f.write(f"    Font: Bold={font.b}, Size={font.sz}, Name={font.name}, Color={font.color.rgb if font.color else 'None'}\n")
                if fill:
                    f.write(f"    Fill: Type={fill.fill_type}, Start={fill.start_color.index if fill.start_color else 'None'}\n")

if __name__ == "__main__":
    inspect_rows()
