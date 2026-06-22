from flask import Flask, request, redirect, make_response
import requests
import sqlite3
import time
import uuid
import os

app = Flask(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN', 'PASTE_BOT_TOKEN')
PIXEL_ID = os.getenv('PIXEL_ID', 'PASTE_PIXEL_ID')
META_TOKEN = os.getenv('META_TOKEN', 'PASTE_META_TOKEN')
YOUR_TELEGRAM_ID = os.getenv('YOUR_TELEGRAM_ID', 'PASTE_YOUR_TELEGRAM_ID')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'wykup_konta_pl_bot')
BASE_URL = os.getenv('BASE_URL', 'https://your-app.onrender.com')
EVENT_NAME = os.getenv('EVENT_NAME', 'Contact')
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
        'INSERT INTO clicks (click_id, fbclid, fbp, ip_address, user_agent, event_source_url, telegram_user_id, lead_sent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (click_id, fbclid, fbp, ip_address, user_agent, event_source_url, None, 0, int(time.time()))
    )
    conn.commit()
    conn.close()

    target = f'https://t.me/{BOT_USERNAME}?start={click_id}'
    response = make_response(redirect(target, code=302))
    if fbp:
        response.set_cookie('_fbp', fbp, max_age=90*24*60*60, secure=True, httponly=False, samesite='Lax')
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
        conn.execute('UPDATE clicks SET telegram_user_id = ? WHERE click_id = ?', (tg_user_id, click_id))
        conn.commit()
        conn.close()
        send_tg_message(tg_user_id, 'Привет! Напишите сообщение, и я передам его менеджеру.')
        return 'ok', 200

    if text and not text.startswith('/'):
        conn = db()
        row = conn.execute(
            'SELECT * FROM clicks WHERE telegram_user_id = ? AND lead_sent = 0 ORDER BY created_at DESC LIMIT 1',
            (tg_user_id,)
        ).fetchone()
        if row:
            send_meta_event(row)
            conn.execute('UPDATE clicks SET lead_sent = 1 WHERE click_id = ?', (row['click_id'],))
            conn.commit()
        conn.close()

        manager_text = f"📩 Новый Telegram лид\nUser ID: {tg_user_id}\nСообщение: {text}"
        send_tg_message(YOUR_TELEGRAM_ID, manager_text)

    return 'ok', 200


def send_tg_message(chat_id, text):
    requests.post(
        f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
        json={'chat_id': chat_id, 'text': text},
        timeout=20
    )


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
