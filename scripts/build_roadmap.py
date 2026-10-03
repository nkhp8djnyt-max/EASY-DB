"""Generate the roadmap in both languages from one source: an HTML page and README blocks.

    python scripts/build_roadmap.py                 # docs/roadmap.html and the roadmap blocks in README*.md
    python scripts/build_roadmap.py --fragment F    # the page without <html>/<head> (for embedding), written to F

The HTML page holds both languages and switches between them with a button (it remembers the
choice and starts in Ukrainian or English depending on the browser language). The README blocks sit
between ``<!-- roadmap:start -->`` and ``<!-- roadmap:end -->``.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.7.0"
TESTS_LIVE = 2158
TESTS_LITE = 1881


@dataclass(frozen=True)
class T:
    """A text in both languages (may contain <code>, <kbd>, <strong>)."""

    uk: str
    en: str


# ---------------------------------------------------------------------------- content

HERO_LEAD = T(
    "Настільний клієнт для PostgreSQL, MySQL/MariaDB і SQLite: редактор SQL, діаграма зв'язків і "
    "редагована сітка. Усі сім етапів початкового плану виконано. Нижче: що ввійшло в кожен етап, "
    "що перевірено лише частково і що варто зробити далі.",
    "A desktop client for PostgreSQL, MySQL/MariaDB and SQLite: an SQL editor, a relationship "
    "diagram and an editable grid. All seven stages of the original plan are done. Below: what "
    "each stage contains, what has only been partly checked, and what to do next.",
)
FACTS = [
    (T("7 із 7", "7 of 7"), T("етапів завершено", "stages complete")),
    (
        T(f"{TESTS_LIVE}", f"{TESTS_LIVE}"),
        T(
            "тестів проходять на живих PostgreSQL і MariaDB",
            "tests pass against live PostgreSQL and MariaDB",
        ),
    ),
    (T("3", "3"), T("діалекти SQL", "SQL dialects")),
    (
        T("2", "2"),
        T(
            "мови інтерфейсу: українська та англійська",
            "interface languages: Ukrainian and English",
        ),
    ),
]

STAGES: list[tuple[T, T, list[T]]] = [
    (
        T("Підключення", "Connections"),
        T(
            "Основа: модель підключення, клієнти баз даних і вікно «Підключення».",
            "The base: the connection model, the database clients and the Connections window.",
        ),
        [
            T(
                "Клієнти PostgreSQL, MySQL/MariaDB і SQLite з єдиним інтерфейсом і зрозумілими помилками.",
                "PostgreSQL, MySQL/MariaDB and SQLite clients behind one interface, with readable errors.",
            ),
            T(
                "Розбір і складання URL підключення; підстановка <code>${ENV}</code> у поля.",
                "Parsing and building connection URLs; <code>${ENV}</code> substitution in fields.",
            ),
            T(
                "Паролі лише в системному keyring або в зашифрованому сховищі, ніколи в JSON.",
                "Passwords only in the system keyring or an encrypted vault, never in JSON.",
            ),
            T(
                "Кнопка «Перевірити» з діагностикою по кроках: DNS, порт, вхід, запит. Вибір бази над діаграмою.",
                "A Test button that diagnoses step by step: DNS, port, sign-in, query. A database switcher above the diagram.",
            ),
        ],
    ),
    (
        T("Редактор SQL", "SQL editor"),
        T(
            "Вкладки запитів, запуск і скасування, таблиця результатів.",
            "Query tabs, running and cancelling, a results table.",
        ),
        [
            T(
                "Підсвічування за діалектом, вкладки відновлюються під час запуску, форматування SQL.",
                "Dialect-aware highlighting, tabs restored on start, SQL formatting.",
            ),
            T(
                "<kbd>Ctrl+Enter</kbd> виконує оператор під курсором, <kbd>F5</kbd> увесь скрипт; результат кожного оператора у своїй вкладці.",
                "<kbd>Ctrl+Enter</kbd> runs the statement under the cursor, <kbd>F5</kbd> the whole script; each statement's result gets its own tab.",
            ),
            T(
                "Захист від небезпечного: <code>DROP</code>, <code>TRUNCATE</code>, <code>DELETE</code> і <code>UPDATE</code> без умови, запис у робочу базу.",
                "Guards against the dangerous: <code>DROP</code>, <code>TRUNCATE</code>, <code>DELETE</code> and <code>UPDATE</code> without a condition, writes to a production database.",
            ),
            T(
                "Необов'язковий сканер на C (AVX2/SSE2/NEON) із запасним варіантом на Python; стенд ClickBench.",
                "An optional C scanner (AVX2/SSE2/NEON) with a pure-Python fallback; a ClickBench rig.",
            ),
        ],
    ),
    (
        T("Схема та ER-діаграма", "Schema and ER diagram"),
        T(
            "Структура бази читається у фоні й малюється як діаграма.",
            "The database structure is read in the background and drawn as a diagram.",
        ),
        [
            T(
                "Таблиці, стовпці, первинні та зовнішні ключі, індекси; зв'язки 1:1 і 1:N, таблиці-зв'язки N:M.",
                "Tables, columns, primary and foreign keys, indexes; 1:1 and 1:N relations, N:M junction tables.",
            ),
            T(
                "Пошарове розкладання, ортогональні лінії, масштаб і переміщення; положення карток запам'ятовується.",
                "Layered layout, orthogonal lines, zoom and pan; card positions are remembered.",
            ),
            T(
                "Пошук за таблицями та стовпцями, вибір схеми, <kbd>Ctrl+P</kbd> для переходу до таблиці, вкладки з даними таблиці.",
                "Search over tables and columns, schema choice, <kbd>Ctrl+P</kbd> to jump to a table, tabs with a table's data.",
            ),
        ],
    ),
    (
        T("Автодоповнення", "Autocomplete"),
        T(
            "Підказки з синтаксису діалекту та зі схеми підключеної бази.",
            "Suggestions from the dialect's syntax and from the connected database's schema.",
        ),
        [
            T(
                "Ключові слова, таблиці, стовпці, псевдоніми та CTE; умова <code>JOIN</code> за зовнішнім ключем; розгортання <code>*</code>.",
                "Keywords, tables, columns, aliases and CTEs; a <code>JOIN</code> condition from the foreign key; <code>*</code> expansion.",
            ),
            T(
                "Контекст курсора витримує недописані запити; сніпети; часті варіанти піднімаються вище.",
                "The cursor context copes with unfinished queries; snippets; frequently chosen items rise to the top.",
            ),
        ],
    ),
    (
        T("Редагована сітка", "Editable grid"),
        T(
            "Правки в таблиці результатів, які не записуються без підтвердження.",
            "Edits in the results table that are not written without confirmation.",
        ),
        [
            T(
                "Правка комірки, новий рядок, видалення, скасування й повтор; редактори під тип: дата, булеве, число.",
                "Cell editing, new rows, deletion, undo and redo; editors that fit the type: date, boolean, number.",
            ),
            T(
                "<kbd>Alt+S</kbd> показує згенеровані <code>UPDATE</code>/<code>INSERT</code>/<code>DELETE</code>, запис іде однією транзакцією: усе або нічого.",
                "<kbd>Alt+S</kbd> shows the generated <code>UPDATE</code>/<code>INSERT</code>/<code>DELETE</code>; everything is written in one transaction: all or nothing.",
            ),
            T(
                "Оптимістичне блокування: якщо рядок змінив хтось інший, транзакція відкочується й називає рядок.",
                "Optimistic locking: if someone else changed a row, the transaction rolls back and names the row.",
            ),
            T(
                "Таблиця без первинного ключа: ключові стовпці можна вибрати вручну.",
                "A table without a primary key: the key columns can be chosen by hand.",
            ),
        ],
    ),
    (
        T("Безпечні підключення", "Secure connections"),
        T(
            "SSL/TLS, SSH-тунель і файли PostgreSQL.",
            "SSL/TLS, the SSH tunnel and the PostgreSQL files.",
        ),
        [
            T(
                "<strong>TLS:</strong> режими від <code>disable</code> до <code>verify-full</code>, CA, клієнтський сертифікат і ключ із парольною фразою. Файли перевіряються до підключення, після входу показується версія TLS і шифр.",
                "<strong>TLS:</strong> modes from <code>disable</code> to <code>verify-full</code>, a CA, a client certificate and a key with a passphrase. The files are checked before connecting; after sign-in the TLS version and cipher are shown.",
            ),
            T(
                "<strong>SSH-тунель:</strong> пароль, ключ або ssh-agent, проміжний вузол (бастіон). Ключ сервера перевіряється до надсилання облікових даних; невідомий ключ потребує явної довіри, ключ, що змінився, блокується.",
                "<strong>SSH tunnel:</strong> password, key or ssh-agent, and a jump host (bastion). The server key is verified before any credentials are sent; an unknown key needs explicit trust, a changed key is refused.",
            ),
            T(
                "<strong>Файли PostgreSQL:</strong> <code>~/.pgpass</code> і <code>pg_service.conf</code>; файл, доступний іншим користувачам, ігнорується, як у libpq.",
                "<strong>PostgreSQL files:</strong> <code>~/.pgpass</code> and <code>pg_service.conf</code>; a file other users can read is ignored, as in libpq.",
            ),
            T(
                "Кроки перевірки підключення: сервіс, джерело пароля, SSH, тунель, файли TLS, шифрування, вхід, запит.",
                "Connection test steps: service, password source, SSH, tunnel, TLS files, encryption, sign-in, query.",
            ),
        ],
    ),
    (
        T("Хмари, історія, експорт, збірка", "Clouds, history, export, build"),
        T(
            "Останній етап плану. Згодом інтерфейс перейшов з російської на українську.",
            "The last stage of the plan. The interface language was later changed from Russian to Ukrainian.",
        ),
        [
            T(
                "<strong>Історія та збережені запити:</strong> <kbd>Ctrl+H</kbd>, <kbd>Ctrl+Shift+H</kbd>, <kbd>Ctrl+S</kbd>. Пошук, лише помилки, папки, область «для одного підключення» або «для всіх».",
                "<strong>History and saved queries:</strong> <kbd>Ctrl+H</kbd>, <kbd>Ctrl+Shift+H</kbd>, <kbd>Ctrl+S</kbd>. Search, failures only, folders, a scope of “one connection” or “all connections”.",
            ),
            T(
                "<strong>Експорт діаграми</strong> (<kbd>Ctrl+E</kbd>): PNG, SVG, PDF, а також текстові Mermaid і DBML.",
                "<strong>Diagram export</strong> (<kbd>Ctrl+E</kbd>): PNG, SVG, PDF, and the text formats Mermaid and DBML.",
            ),
            T(
                "<strong>Хмарні провайдери:</strong> AWS RDS/Aurora, Google Cloud SQL і Azure Entra працюють через токен замість пароля. Для Supabase, Neon, PlanetScale і CockroachDB підставляються шаблони полів.",
                "<strong>Cloud providers:</strong> AWS RDS/Aurora, Google Cloud SQL and Azure Entra work through a token instead of a password. Supabase, Neon, PlanetScale and CockroachDB get field templates.",
            ),
            T(
                "<strong>Тема «Системна»</strong> слідує за світлою чи темною темою ОС і перемикається на льоту.",
                "<strong>The System theme</strong> follows the operating system's light or dark setting and switches live.",
            ),
            T(
                "<strong>Пакування:</strong> PyInstaller, <code>scripts/build_app.py</code>, CI на кожен коміт, збірка під Linux, Windows і macOS на вимогу.",
                "<strong>Packaging:</strong> PyInstaller, <code>scripts/build_app.py</code>, CI on every commit, Linux, Windows and macOS builds on demand.",
            ),
        ],
    ),
]

# area, (status class, label), detail
CHECKED: list[tuple[T, str, T, T]] = [
    (
        T("PostgreSQL 16, MariaDB 10.11, SQLite", "PostgreSQL 16, MariaDB 10.11, SQLite"),
        "done",
        T("наживо", "live"),
        T(
            "Увесь набір тестів, зокрема правки в сітці, схема, TLS і тунель, на справжніх серверах.",
            "The whole test suite, including grid editing, schema, TLS and tunnel, against real servers.",
        ),
    ),
    (
        T("TLS і клієнтські сертифікати", "TLS and client certificates"),
        "done",
        T("наживо", "live"),
        T(
            "PostgreSQL і MariaDB із тестовим центром сертифікації: <code>verify-ca</code>, <code>verify-full</code>, вхід за сертифікатом, зашифрований ключ.",
            "PostgreSQL and MariaDB with a test certificate authority: <code>verify-ca</code>, <code>verify-full</code>, certificate login, an encrypted key.",
        ),
    ),
    (
        T("SSH-тунель", "SSH tunnel"),
        "part",
        T("частково", "partly"),
        T(
            "Проти SSH-сервера й ssh-agent, написаних для тестів на paramiko; за тунелем справжні бази. Системний OpenSSH не використовувався.",
            "Against an SSH server and ssh-agent written for the tests (paramiko); real databases behind the tunnel. The system OpenSSH was not used.",
        ),
    ),
    (
        T("AWS RDS (IAM)", "AWS RDS (IAM)"),
        "part",
        T("частково", "partly"),
        T(
            "Токен підписується справжнім boto3 локально. Вхід у живий кластер RDS не перевірявся.",
            "The token is signed locally by the real boto3. Signing in to a live RDS cluster was not tried.",
        ),
    ),
    (
        T("Azure Entra, Google Cloud SQL", "Azure Entra, Google Cloud SQL"),
        "part",
        T("на заглушках", "on stand-ins"),
        T(
            "Логіку токенів і помилок перевірено на підроблених модулях SDK; хмарних акаунтів не було.",
            "Token and error logic checked against fake SDK modules; no cloud accounts were available.",
        ),
    ),
    (
        T("Supabase, Neon, PlanetScale, CockroachDB", "Supabase, Neon, PlanetScale, CockroachDB"),
        "open",
        T("не перевірено", "not checked"),
        T(
            "Перевірено лише значення, які підставляють шаблони. До справжніх сервісів не підключалися.",
            "Only the values the templates fill in are tested. No real service was connected.",
        ),
    ),
    (
        T("Oracle MySQL", "Oracle MySQL"),
        "open",
        T("не перевірено", "not checked"),
        T(
            "Діалект MySQL перевірено на MariaDB 10.11; сервери Oracle MySQL не запускалися.",
            "The MySQL dialect is tested on MariaDB 10.11; Oracle MySQL servers were not run.",
        ),
    ),
    (
        T("Збірка застосунку", "Application build"),
        "part",
        T("лише Linux", "Linux only"),
        T(
            "На Linux PyInstaller зібрав застосунок, він запускається й друкує версію. Воркфлоу для Windows і macOS написано, але жодного разу не запускано.",
            "On Linux PyInstaller builds the app; it starts and prints its version. The Windows and macOS workflow is written but has never run.",
        ),
    ),
    (
        T("Швидкість (ClickBench, схема)", "Speed (ClickBench, schema)"),
        "open",
        T("відкладено", "postponed"),
        T(
            "Стенди готові, прогони відкладено до окремого запуску.",
            "The benchmark rigs are ready; the runs are postponed until they are started separately.",
        ),
    ),
]

NEXT_INTRO = T(
    "Це пропозиції, а не зобов'язання. Розмір S, M, L — груба оцінка обсягу роботи; порядок усередині групи відображає цінність для користувача.",
    "These are proposals, not commitments. The size S, M, L is a rough estimate of the work; the order inside a group reflects the value to users.",
)
NEXT: list[tuple[T, T, list[tuple[T, str, T]]]] = [
    (
        T("Закрити прогалини", "Close the gaps"),
        T(
            "Дороблення перевірок, без яких етапи 6 і 7 не можна вважати закритими остаточно.",
            "The checks that stages 6 and 7 still need before they count as fully closed.",
        ),
        [
            (
                T("Збірки Windows і macOS", "Windows and macOS builds"),
                "S",
                T(
                    "Запустити воркфлоу збірки на всіх трьох системах і переконатися, що застосунок стартує.",
                    "Run the build workflow on all three systems and make sure the app starts.",
                ),
            ),
            (
                T("Справжні хмарні акаунти", "Real cloud accounts"),
                "S",
                T(
                    "Один прогін входу в RDS, Azure і Cloud SQL, а потім у Supabase, Neon, PlanetScale і CockroachDB.",
                    "One sign-in run against RDS, Azure and Cloud SQL, then Supabase, Neon, PlanetScale and CockroachDB.",
                ),
            ),
            (
                T("Системний OpenSSH", "System OpenSSH"),
                "S",
                T(
                    "Прогнати тунель і проміжний вузол проти справжнього <code>sshd</code> замість тестового сервера.",
                    "Run the tunnel and the jump host against a real <code>sshd</code> instead of the test server.",
                ),
            ),
            (
                T("Бенчмарки", "Benchmarks"),
                "S",
                T(
                    "Запустити ClickBench і тести схеми та зберегти результати поруч із кодом.",
                    "Run ClickBench and the schema tests and keep the results next to the code.",
                ),
            ),
        ],
    ),
    (
        T("Нові можливості", "New features"),
        T(
            "Те, чого застосунку бракує поряд зі зрілими клієнтами баз даних.",
            "What the app still lacks next to mature database clients.",
        ),
        [
            (
                T("Експорт результатів у файл", "Export results to a file"),
                "S",
                T(
                    "CSV, JSON, XLSX і Parquet із сітки. Копіювання в CSV і Markdown уже є.",
                    "CSV, JSON, XLSX and Parquet from the grid. Copying as CSV and Markdown already exists.",
                ),
            ),
            (
                T("План запиту", "Query plan"),
                "M",
                T(
                    "<code>EXPLAIN</code> і <code>EXPLAIN ANALYZE</code> деревом, з підсвічуванням найдорожчих вузлів.",
                    "<code>EXPLAIN</code> and <code>EXPLAIN ANALYZE</code> as a tree, with the most expensive nodes highlighted.",
                ),
            ),
            (
                T("Імпорт CSV у таблицю", "CSV import into a table"),
                "M",
                T(
                    "Зіставлення стовпців, попередній перегляд, завантаження однією транзакцією.",
                    "Column mapping, a preview, loading in one transaction.",
                ),
            ),
            (
                T("Редактор таблиць", "Table designer"),
                "L",
                T(
                    "Створення й зміна таблиць, індексів і ключів з попереднім переглядом DDL перед застосуванням.",
                    "Creating and changing tables, indexes and keys, with a DDL preview before applying.",
                ),
            ),
            (
                T("Порівняння схем і міграції", "Schema diff and migrations"),
                "L",
                T(
                    "Відмінності двох баз і згенерований скрипт міграції.",
                    "The differences between two databases and a generated migration script.",
                ),
            ),
        ],
    ),
    (
        T("Випуск", "Release"),
        T(
            "Щоб застосунком можна було користуватися без Python.",
            "So the app can be used without Python.",
        ),
        [
            (
                T("Інсталятори з підписом", "Signed installers"),
                "M",
                T(
                    "MSI або NSIS для Windows, DMG з нотаризацією для macOS, AppImage для Linux.",
                    "MSI or NSIS for Windows, a notarised DMG for macOS, an AppImage for Linux.",
                ),
            ),
            (
                T("Автооновлення", "Auto-update"),
                "M",
                T(
                    "Перевірка нової версії та оновлення без ручного перевстановлення.",
                    "Checking for a new version and updating without a manual reinstall.",
                ),
            ),
            (
                T("Cloud SQL Connector", "Cloud SQL Connector"),
                "M",
                T(
                    "Вбудувати конектор Google замість підключення за IP або через Auth Proxy.",
                    "Embed Google's connector instead of connecting by IP or through the Auth Proxy.",
                ),
            ),
            (
                T("Файли DuckDB", "DuckDB files"),
                "M",
                T(
                    "Четвертий діалект. Архітектура клієнтів це дозволяє, у початковому плані його було відкладено.",
                    "A fourth dialect. The client architecture allows it; the original plan postponed it.",
                ),
            ),
        ],
    ),
]
NOT_PLANNED_INTRO = T(
    "Рішення, ухвалені на старті проєкту.", "Decisions made at the start of the project."
)
NOT_PLANNED = [
    T(
        "<strong>SQL Server, Oracle, ClickHouse.</strong> Корпоративні діалекти виключено: застосунок підтримує три діалекти.",
        "<strong>SQL Server, Oracle, ClickHouse.</strong> Corporate dialects are out of scope: the app supports three dialects.",
    ),
]

UI = {
    "title": T("Дорожня карта EasyDBMS", "EasyDBMS roadmap"),
    "done": T("Що зроблено", "What is done"),
    "done_lead": T(
        "Етапи йшли по порядку: кожен будувався на попередньому й закінчувався прогоном тестів, знімками екрана та записом у документації.",
        "The stages went in order: each built on the previous one and ended with a test run, screenshots and documentation.",
    ),
    "checked": T("Що перевірено, а що ні", "What is verified and what is not"),
    "checked_lead": T(
        "Тести зелені, але не все в них однаково близьке до реальності. Тут видно, де перевірка справжня, а де її замінює заглушка.",
        "The tests are green, but not everything in them is equally close to reality. This shows where a check is real and where a stand-in replaces it.",
    ),
    "area": T("Область", "Area"),
    "status": T("Статус", "Status"),
    "how": T("Чим перевірено", "How it was checked"),
    "next": T("Що пропонується далі", "What is proposed next"),
    "no": T("Не планується", "Not planned"),
    "done_pill": T("готово", "done"),
    "footer": T(
        f"Версія {VERSION}. Тести: {TESTS_LIVE} із живими PostgreSQL і MariaDB, {TESTS_LITE} на самому SQLite; <code>ruff</code> і <code>mypy</code> без зауважень.",
        f"Version {VERSION}. Tests: {TESTS_LIVE} with live PostgreSQL and MariaDB, {TESTS_LITE} on SQLite alone; <code>ruff</code> and <code>mypy</code> report nothing.",
    ),
    "switch": T("Мова", "Language"),
}


# ---------------------------------------------------------------------------- HTML


def both(text: T, tag: str = "span") -> str:
    return f'<{tag} data-l="uk">{text.uk}</{tag}><{tag} data-l="en">{text.en}</{tag}>'


CSS = """
  :root {
    --bg: #f4f7fb; --surface: #ffffff; --ink: #13202f; --muted: #566880; --line: #d8e0eb;
    --accent: #2a66e0; --accent-soft: #e3ecfd;
    --done: #17784f; --done-soft: #def2e8; --part: #8f5a00; --part-soft: #faefd2;
    --open: #4a5b72; --open-soft: #e6ecf4;
    --font-display: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
    --font-body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0d1520; --surface: #142030; --ink: #e4ecf6; --muted: #94a6bc; --line: #25354a;
      --accent: #6c9cff; --accent-soft: #16294a;
      --done: #4fc58b; --done-soft: #0f2e22; --part: #e3ab40; --part-soft: #33280f;
      --open: #9fb0c6; --open-soft: #1b2839; color-scheme: dark;
    }
  }
  :root[data-theme="dark"] {
    --bg: #0d1520; --surface: #142030; --ink: #e4ecf6; --muted: #94a6bc; --line: #25354a;
    --accent: #6c9cff; --accent-soft: #16294a;
    --done: #4fc58b; --done-soft: #0f2e22; --part: #e3ab40; --part-soft: #33280f;
    --open: #9fb0c6; --open-soft: #1b2839; color-scheme: dark;
  }
  /* one language at a time: Ukrainian unless the page chose English */
  :root:not([data-ui="en"]) [data-l="en"], :root[data-ui="en"] [data-l="uk"] { display: none !important; }

  * { box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--ink); font: 15px/1.6 var(--font-body);
    margin: 0; padding-inline: clamp(16px, 4vw, 32px); padding-block: 40px 64px;
  }
  .page { max-width: 960px; margin-inline: auto; display: flex; flex-direction: column; gap: 56px; }
  h1, h2, h3 { font-family: var(--font-display); font-weight: 600; text-wrap: balance; margin: 0; line-height: 1.15; }
  p { margin: 0; }
  code, kbd { font-family: var(--font-mono); font-size: 0.86em; }
  kbd { background: var(--open-soft); border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; white-space: nowrap; }
  code { background: var(--open-soft); border-radius: 4px; padding: 1px 5px; overflow-wrap: anywhere; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px 32px; align-items: start; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .eyebrow { font: 500 12px/1.4 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
  .lang { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--surface); }
  .lang button {
    font: 500 12px/1 var(--font-mono); letter-spacing: 0.06em; padding: 8px 12px; border: 0;
    background: transparent; color: var(--muted); cursor: pointer;
  }
  .lang button + button { border-left: 1px solid var(--line); }
  .lang button[aria-pressed="true"] { background: var(--accent); color: var(--bg); }
  h1 { font-size: clamp(34px, 6vw, 52px); margin-top: 10px; }
  .lead { color: var(--muted); max-width: 62ch; margin-top: 14px; font-size: 16.5px; }
  .mark { width: 112px; height: 88px; color: var(--accent); flex: none; }
  .facts { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 0; padding-top: 18px; border-top: 1px solid var(--line); }
  .facts div { display: flex; gap: 8px; align-items: baseline; }
  .facts dt { font: 600 20px/1 var(--font-display); font-variant-numeric: tabular-nums; }
  .facts dd { margin: 0; color: var(--muted); font-size: 13.5px; }
  @media (max-width: 560px) { header { grid-template-columns: 1fr; } .mark { display: none; } }

  .section-head { display: flex; flex-direction: column; gap: 6px; margin-bottom: 20px; }
  h2 { font-size: 27px; }
  .section-head p { color: var(--muted); max-width: 66ch; }

  .stages { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .stage { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 0 18px; }
  .rail { position: relative; display: flex; justify-content: center; }
  .rail::before { content: ""; position: absolute; top: 0; bottom: 0; width: 2px; background: var(--line); }
  .stage:first-child .rail::before { top: 22px; }
  .stage:last-child .rail::before { bottom: calc(100% - 22px); }
  .node {
    position: relative; z-index: 1; margin-top: 4px; width: 36px; height: 36px; border-radius: 9px;
    background: var(--done); color: var(--bg); display: grid; place-items: center;
    font: 600 17px/1 var(--font-display); font-variant-numeric: tabular-nums;
  }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .card-top { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 16px; }
  h3 { font-size: 21px; }
  .summary { color: var(--muted); }
  .card ul { margin: 0; padding-left: 1.15em; display: flex; flex-direction: column; gap: 5px; }
  .card li::marker { color: var(--accent); }
  .card li { padding-left: 2px; }
  .card strong { font-weight: 600; }

  .pill { display: inline-flex; align-items: center; gap: 6px; font: 500 12px/1 var(--font-mono); padding: 5px 9px; border-radius: 999px; white-space: nowrap; }
  .pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .pill.done { color: var(--done); background: var(--done-soft); }
  .pill.part { color: var(--part); background: var(--part-soft); }
  .pill.open { color: var(--open); background: var(--open-soft); }

  .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
  table { width: 100%; border-collapse: collapse; min-width: 640px; }
  th, td { text-align: left; padding: 12px 16px; vertical-align: top; border-bottom: 1px solid var(--line); }
  tr:last-child td { border-bottom: 0; }
  th { font: 500 12px/1.3 var(--font-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); background: var(--open-soft); }
  td:first-child { font-weight: 500; width: 30%; }
  td:nth-child(2) { width: 18%; }
  td:last-child { color: var(--muted); }

  .board { display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 16px; align-items: start; }
  .group { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 18px 18px 8px; min-width: 0; }
  .group h3 { font-size: 19px; }
  .group > p { color: var(--muted); font-size: 13.5px; margin: 6px 0 14px; }
  .item { padding: 14px 0; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 5px; }
  .item-top { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
  .item h4 { margin: 0; font: 600 15px/1.35 var(--font-body); }
  .item p { color: var(--muted); font-size: 14px; }
  .size { font: 500 12px/1 var(--font-mono); color: var(--accent); background: var(--accent-soft); border-radius: 5px; padding: 4px 7px; flex: none; }
  .no { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
  .no li { padding: 12px 0; border-top: 1px solid var(--line); color: var(--muted); font-size: 14px; }
  .no li strong { color: var(--ink); font-weight: 500; }
  footer { color: var(--muted); font-size: 13px; border-top: 1px solid var(--line); padding-top: 18px; }

  @media (max-width: 520px) {
    .stage { grid-template-columns: 30px minmax(0, 1fr); gap: 0 12px; }
    .node { width: 30px; height: 30px; font-size: 15px; }
    .card { padding: 16px; }
  }
"""

SCRIPT = """
(function () {
  var root = document.documentElement;
  var KEY = "easydbms-roadmap-lang";
  function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function remember(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode: the choice just is not kept */ } }
  var titles = %TITLES%;
  function apply(lang) {
    root.setAttribute("data-ui", lang);
    root.setAttribute("lang", lang);
    document.title = titles[lang];
    var buttons = document.querySelectorAll(".lang button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].getAttribute("data-set") === lang ? "true" : "false");
    }
  }
  var wanted = saved();
  if (wanted !== "uk" && wanted !== "en") {
    wanted = /^uk\\b/i.test(navigator.language || "") ? "uk" : "en";
  }
  apply(wanted);
  var buttons = document.querySelectorAll(".lang button");
  for (var i = 0; i < buttons.length; i++) {
    buttons[i].addEventListener("click", function (event) {
      var lang = event.currentTarget.getAttribute("data-set");
      apply(lang);
      remember(lang);
    });
  }
})();
"""

MARK = (
    '<svg class="mark" viewBox="0 0 112 88" role="img" aria-label="ER diagram">'
    '<g fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">'
    '<path d="M34 22h44M30 32l14 28M82 32L68 60"/>'
    '<rect x="4" y="8" width="32" height="26" rx="5"/><path d="M4 16h32"/>'
    '<rect x="76" y="8" width="32" height="26" rx="5"/><path d="M76 16h32"/>'
    '<rect x="40" y="56" width="32" height="26" rx="5"/><path d="M40 64h32"/>'
    "</g></svg>"
)


def render_body() -> str:
    out: list[str] = ['<div class="page">', "  <header>", "    <div>"]
    out.append('      <div class="top">')
    out.append(f'        <p class="eyebrow">EasyDBMS {VERSION} · Python 3.12 · PySide6</p>')
    out.append(
        '        <div class="lang" role="group" aria-label="Language / Мова">'
        '<button type="button" data-set="uk" aria-pressed="true">УКР</button>'
        '<button type="button" data-set="en" aria-pressed="false">ENG</button></div>'
    )
    out.append("      </div>")
    out.append(f"      <h1>{both(UI['title'])}</h1>")
    out.append(f'      <p class="lead">{both(HERO_LEAD)}</p>')
    out.append("    </div>")
    out.append("    " + MARK)
    out.append('    <dl class="facts">')
    for value, label in FACTS:
        out.append(f"      <div><dt>{both(value)}</dt><dd>{both(label)}</dd></div>")
    out.append("    </dl>")
    out.append("  </header>")

    out.append('  <section aria-labelledby="done"><div class="section-head">')
    out.append(f'    <h2 id="done">{both(UI["done"])}</h2><p>{both(UI["done_lead"])}</p></div>')
    out.append('    <ol class="stages">')
    for number, (title, summary, items) in enumerate(STAGES, start=1):
        out.append(
            f'      <li class="stage"><div class="rail"><span class="node">{number}</span></div>'
        )
        out.append('        <article class="card">')
        out.append(
            f'          <div class="card-top"><h3>{both(title)}</h3>'
            f'<span class="pill done">{both(UI["done_pill"])}</span></div>'
        )
        out.append(f'          <p class="summary">{both(summary)}</p>')
        out.append("          <ul>")
        out.extend(f"            <li>{both(item)}</li>" for item in items)
        out.append("          </ul></article></li>")
    out.append("    </ol></section>")

    out.append('  <section aria-labelledby="checked"><div class="section-head">')
    out.append(
        f'    <h2 id="checked">{both(UI["checked"])}</h2><p>{both(UI["checked_lead"])}</p></div>'
    )
    out.append('    <div class="table-wrap"><table><thead><tr>')
    out.append(
        f'<th scope="col">{both(UI["area"])}</th><th scope="col">{both(UI["status"])}</th>'
        f'<th scope="col">{both(UI["how"])}</th></tr></thead><tbody>'
    )
    for area, kind, label, detail in CHECKED:
        out.append(
            f'      <tr><td>{both(area)}</td><td><span class="pill {kind}">{both(label)}</span></td>'
            f"<td>{both(detail)}</td></tr>"
        )
    out.append("    </tbody></table></div></section>")

    out.append('  <section aria-labelledby="next"><div class="section-head">')
    out.append(f'    <h2 id="next">{both(UI["next"])}</h2><p>{both(NEXT_INTRO)}</p></div>')
    out.append('    <div class="board">')
    for title, intro, items in NEXT:
        out.append(f'      <div class="group"><h3>{both(title)}</h3><p>{both(intro)}</p>')
        for name, size, text in items:
            out.append(
                f'        <div class="item"><div class="item-top"><h4>{both(name)}</h4>'
                f'<span class="size">{size}</span></div><p>{both(text)}</p></div>'
            )
        out.append("      </div>")
    out.append("    </div></section>")

    out.append('  <section aria-labelledby="no"><div class="section-head">')
    out.append(f'    <h2 id="no">{both(UI["no"])}</h2><p>{both(NOT_PLANNED_INTRO)}</p></div>')
    out.append('    <ul class="no">')
    out.extend(f"      <li>{both(item)}</li>" for item in NOT_PLANNED)
    out.append("    </ul></section>")
    out.append(f"  <footer><p>{both(UI['footer'])}</p></footer>")
    out.append("</div>")
    return "\n".join(out)


def render_script() -> str:
    titles = f'{{"uk": "{UI["title"].uk}", "en": "{UI["title"].en}"}}'
    return SCRIPT.replace("%TITLES%", titles)


def standalone_page() -> str:
    return (
        '<!doctype html>\n<html lang="uk">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{UI['title'].uk}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{render_body()}\n<script>{render_script()}</script>\n</body>\n</html>\n"
    )


def fragment_page() -> str:
    """For hosts that wrap the page themselves (no doctype / html / head)."""
    fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
        '&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">\n'
    )
    return (
        f"<title>{UI['title'].uk}</title>\n{fonts}<style>{CSS}</style>\n"
        f"{render_body()}\n<script>{render_script()}</script>\n"
    )


# ---------------------------------------------------------------------------- Markdown


def md(text: str) -> str:
    text = re.sub(r"</?(?:code|kbd)>", "`", text)
    text = re.sub(r"</?strong>", "**", text)
    return text.replace("|", "\\|")


def render_markdown(lang: str) -> str:
    def pick(text: T) -> str:
        return md(getattr(text, lang))

    lines: list[str] = []
    lines.append(f"### {pick(UI['done'])}")
    lines.append("")
    lines.append(pick(UI["done_lead"]))
    lines.append("")
    for number, (title, summary, items) in enumerate(STAGES, start=1):
        lines.append(f"**{number}. {pick(title)}** — {pick(summary)}")
        lines.append("")
        lines.extend(f"- {pick(item)}" for item in items)
        lines.append("")
    lines.append(f"### {pick(UI['checked'])}")
    lines.append("")
    lines.append(pick(UI["checked_lead"]))
    lines.append("")
    lines.append(f"| {pick(UI['area'])} | {pick(UI['status'])} | {pick(UI['how'])} |")
    lines.append("|---|---|---|")
    icons = {"done": "✅", "part": "🟡", "open": "⬜"}
    for area, kind, label, detail in CHECKED:
        lines.append(f"| {pick(area)} | {icons[kind]} {pick(label)} | {pick(detail)} |")
    lines.append("")
    lines.append(f"### {pick(UI['next'])}")
    lines.append("")
    lines.append(pick(NEXT_INTRO))
    lines.append("")
    for title, intro, items in NEXT:
        lines.append(f"**{pick(title)}.** {pick(intro)}")
        lines.append("")
        lines.extend(f"- **{pick(n)}** (`{size}`) — {pick(text)}" for n, size, text in items)
        lines.append("")
    lines.append(f"### {pick(UI['no'])}")
    lines.append("")
    lines.append(pick(NOT_PLANNED_INTRO))
    lines.append("")
    lines.extend(f"- {pick(item)}" for item in NOT_PLANNED)
    return "\n".join(lines).rstrip() + "\n"


MARKERS = re.compile(r"(<!-- roadmap:start -->\n).*?(<!-- roadmap:end -->)", re.S)


def update_readme(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = render_markdown(lang)
    updated, count = MARKERS.subn(lambda m: m.group(1) + "\n" + block + "\n" + m.group(2), text)
    if count:
        path.write_text(updated, encoding="utf-8")
    return bool(count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fragment", type=Path, help="write the page without <html>/<head> here")
    args = parser.parse_args()
    if args.fragment:
        args.fragment.write_text(fragment_page(), encoding="utf-8")
        print(f"wrote {args.fragment}")
        return
    (ROOT / "docs" / "roadmap.html").write_text(standalone_page(), encoding="utf-8")
    print("wrote docs/roadmap.html")
    for name, lang in (("README.md", "en"), ("README.uk.md", "uk")):
        print(f"{name}: {'updated' if update_readme(ROOT / name, lang) else 'no markers'}")


if __name__ == "__main__":
    main()
