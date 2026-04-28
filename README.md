# Prey (2017) — Thai Translation Mod

ไทยทั้งเกมสำหรับ Prey (2017, Arkane Studios) ครอบคลุมเมนู, ภารกิจ, ตัวละคร, audio log, email, books, lore, dialog (voices)

แปลด้วย Gemini 2.5 Flash + glossary คุมศัพท์เฉพาะของเกม (Typhon, Neuromod, Mimic, Coral, ฯลฯ คงเป็นภาษาอังกฤษ)

## Install (สำหรับผู้เล่น)

1. ไปโฟลเดอร์เกม: `<Steam>\steamapps\common\Prey\Localization`
2. **Backup ของเดิม** — copy `English_xml_patch.pak` เก็บไว้ก่อน เผื่อต้องการกลับ
3. Copy `release/English_xml_patch.pak` ไปทับ `Localization/English_xml_patch.pak`
4. เปิดเกม → ภาษาเลือก English (เกมยังไม่รองรับ Thai มาเอง — mod แทนที่ภาษาอังกฤษด้วยไทย)

## Build / Re-translate (สำหรับนักพัฒนา)

```bash
cd source
pip install -r requirements.txt
echo "GEMINI_API_KEY=YOUR_KEY" > .env   # ขอ key ฟรีที่ aistudio.google.com/apikey

# แปล (resume ได้ — รัน Ctrl-C แล้วรันใหม่ก็ต่อจากเดิม)
python translate_all.py --batch-size 150 --concurrency 10 --rpm 600

# pack เป็น .pak ไฟล์เดียวพร้อม XML + font Thai
python pack_xml.py
```

## Files

```
release/
  English_xml_patch.pak        — ไฟล์แปล + font Thai ในไฟล์เดียว (ทับของเดิม)
  patch_thai_font_actual.zip   — font Thai source package สำหรับ rebuild .pak
source/
  loc_src/                     — XML 171 ไฟล์ที่แปลแล้ว
  translate_all.py             — สคริปต์แปลด้วย Gemini API
  pack_xml.py                  — pack loc_src → .pak
  glossary.json                — keep-English terms + fixed translations
  style_prompt.md              — system prompt คุมโทน + pronoun
  requirements.txt
```

## Glossary

คำที่ **คงเป็นภาษาอังกฤษ:** Typhon, Talos I, Coral, Apex, Nullwave, TranScribe, Neuromod, Mimic, Phantom, Weaver, Telepath, Technopath, Operator, Nightmare, Psychotronics, Arboretum, Bridge, Power Plant, GLOO, Q-Beam, Recycler, Fabricator, Arming Key + ชื่อตัวละคร (Alex, Morgan, January, Dahl, Sarah Elazar, ฯลฯ)

## Credits

- Translation pipeline: Gemini 2.5 Flash
- Font: Noto Sans Thai (Google)
- Original game: Arkane Studios / Bethesda

## License

ไฟล์ในโฟลเดอร์ `release/` เป็น modification ของ Prey — สิทธิ์เกมเป็นของ Bethesda  
สคริปต์ + glossary + style prompt ใน `source/` ปล่อยฟรี ใช้ต่อได้
