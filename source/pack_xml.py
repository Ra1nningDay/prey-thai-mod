import os
import zipfile

src_dir = r"D:\Steam\steamapps\common\Prey\_thai_mod_work\loc_src"
zip_path = r"D:\Steam\steamapps\common\Prey\_thai_mod_work\English_xml_patch.zip"

with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(src_dir):
        for file in files:
            file_path = os.path.join(root, file)
            # Create a relative path from the source directory
            rel_path = os.path.relpath(file_path, src_dir)
            zipf.write(file_path, arcname=rel_path)
            
print(f"Created {zip_path}")
