import sqlite3, json
conn = sqlite3.connect('knowledge/index.db')
row = conn.execute("SELECT _content FROM _rows WHERE table_name='NpcTalk' LIMIT 1").fetchone()
if row:
    print(json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2)[:800])
else:
    print('not found')
conn.close()
