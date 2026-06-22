from flask import Flask, request, redirect, make_response
import requests
import sqlite3
import time
import uuid
import os
import urllib.parse

app = Flask(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
PIXEL_ID = os.getenv('PIXEL_ID', '')
META_TOKEN = os.getenv('META_TOKEN', '')
BOT_USERNAME = os.getenv('BOT_USERNAME', '')
BASE_URL = os.getenv('BASE_URL', 'https://tg-meta-bot.onrender.com')
EVENT_NAME = os.getenv('EVENT_NAME', 'Contact')
MANAGER_USERNAME = os.getenv('MANAGER_USERNAME', 'bussines_kurier_pl')
DB_PATH = os.getenv('DB_PATH', 'leads.db')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS clicks (
            click_id TEXT PRIMARY KEY,
            fbclid TEXT,
            fbp TEXT,
            ip_address TEXT,
            user_agent TEXT,
            event_source_url TEXT,
            telegram_user_id TEXT,
            lead_sent INTEGER DEFAULT 0,
            created_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()


init_db()


@app.route('/')
def home():
    return {'ok': True, 'service': 'telegram-meta-capi'}


@app.route('/go')
def go():
    fbclid = request.args.get('fbclid', '')
    fbp = request.args.get('fbp', '') or request.cookies.get('_fbp', '')
    click_id = str(uuid.uuid4())[:12]
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    user_agent = request.headers.get('User-Agent', '')
    event_source_url = request.url

    conn = db()
    conn.execute(
        '''
        INSERT INTO clicks
        (click_id, fbclid, fbp, ip_address, user_agent, event_source_url, telegram_user_id, lead_sent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (click_id, fbclid, fbp, ip_address, user_agent, event_source_url, None, 0, int(time.time()))
    )
    conn.commit()
    conn.close()

    target = f'https://t.me/{BOT_USERNAME}?start={click_id}'
    response = make_response(redirect(target, code=302))

    if fbp:
        response.set_cookie(
            '_fbp',
            fbp,
            max_age=90 * 24 * 60 * 60,
            secure=True,
            httponly=False,
            samesite='Lax'
        )

    return response


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True) or {}
    message = data.get('message')

    if not message:
        return 'ok', 200

    tg_user_id = str(message.get('from', {}).get('id', ''))
    text = message.get('text', '') or ''

    if text.startswith('/start '):
        click_id = text.split(' ', 1)[1].strip()

        conn = db()
        conn.execute(
            'UPDATE clicks SET telegram_user_id = ? WHERE click_id = ?',
            (tg_user_id, click_id)
        )
        conn.commit()
        conn.close()

        button_url = f"{BASE_URL}/to_manager?click_id={click_id}"

        keyboard = {
            "inline_keyboard": [[
                {
                    "text": "💬 Przejdź do managera",
                    "url": button_url
                }
            ]]
        }

        welcome_text = (
            "Cześć! ✨\n\n"
            "Jeśli chcesz szybko otrzymać odpowiedź i szczegóły oferty, "
            "kliknij przycisk poniżej i przejdź bezpośrednio do rozmowy z managerem. 🚀"
        )

        requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            json={
                'chat_id': tg_user_id,
                'text': welcome_text,
                'reply_markup': keyboard
            },
            timeout=20
        )

    return 'ok', 200


@app.route('/to_manager')
def to_manager():
    click_id = request.args.get('click_id', '')

    conn = db()
    row = conn.execute(
        'SELECT * FROM clicks WHERE click_id = ? LIMIT 1',
        (click_id,)
    ).fetchone()

    if row and int(row['lead_sent'] or 0) == 0:
        send_meta_event(row)
        conn.execute(
            'UPDATE clicks SET lead_sent = 1 WHERE click_id = ?',
            (click_id,)
        )
        conn.commit()

    conn.close()

    ready_text = urllib.parse.quote(
        "Cześć! 👋 Piszę z reklamy i chcę otrzymać więcej informacji."
    )
    manager_link = f"https://t.me/{MANAGER_USERNAME}?text={ready_text}"

    return redirect(manager_link, code=302)


def send_meta_event(row):
    fbc = ''
    if row['fbclid']:
        fbc = f"fb.1.{int(time.time())}.{row['fbclid']}"

    payload = {
        'data': [{
            'event_name': EVENT_NAME,
            'event_time': int(time.time()),
            'action_source': 'website',
            'event_source_url': row['event_source_url'] or BASE_URL,
            'user_data': {
                'client_ip_address': row['ip_address'] or '',
                'client_user_agent': row['user_agent'] or '',
                'external_id': row['telegram_user_id'] or '',
                'fbc': fbc,
                'fbp': row['fbp'] or ''
            }
        }]
    }

    requests.post(
        f'https://graph.facebook.com/v23.0/{PIXEL_ID}/events',
        params={'access_token': META_TOKEN},
        json=payload,
        timeout=20
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
