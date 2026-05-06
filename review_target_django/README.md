# Review Target Django App

Ez egy szándékosan egyszerű, működő Django to-do / mini projektkezelő alkalmazás, amely alkalmas automatikus code-reviewer tesztelésére GitHub Actions-ben.

A projektben direkt vannak olyan részek, amelyekre egy LLM-alapú reviewer, statikus analyzer vagy dinamikus tesztelő jó kommenteket tud adni:

- ismétlődő view logika
- N+1 query problémák
- hiányos jogosultságkezelés
- gyenge input validáció
- túl hosszú függvények
- magic stringek és magic numberök
- néhol rossz exception kezelés
- biztonságilag rossz beállítások dev/prod szeparáció nélkül
- részben hiányzó tesztek
- optimalizálható model/query használat

## Futtatás lokálisan

```bash
cd taskmanager
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo_data
python manage.py runserver
```

Belépés:

- admin / admin12345
- alice / alice12345
- bob / bob12345

## GitHub Actions

A `.github/workflows/code-review.yml` workflow futtat:

- Django teszteket
- ruff lintet
- bandit security scan-t
- pip-audit dependency auditot

Ez a repo direkt úgy van megírva, hogy a workflow és egy automatikus code reviewer találjon fejleszthető pontokat.
