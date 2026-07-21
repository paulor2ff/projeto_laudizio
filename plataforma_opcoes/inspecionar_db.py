import sqlite3

conn = sqlite3.connect("opcoes_b3.db")
cur = conn.cursor()

print("=== Tabelas ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tabelas = [row[0] for row in cur.fetchall()]
print(tabelas)

for tabela in tabelas:
    print(f"\n=== {tabela} ===")

    try:
        qtd = cur.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
        print("Registros:", qtd)

        linhas = cur.execute(f"SELECT * FROM {tabela} LIMIT 3").fetchall()
        for linha in linhas:
            print(linha)

    except Exception as e:
        print(e)

conn.close()