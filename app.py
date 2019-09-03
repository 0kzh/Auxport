import spotipy
import spotipy.oauth2 as oauth2
from flask import Flask, render_template, session, redirect, url_for, request, g
from flask_mail import Mail, Message
import datetime
from dateutil.relativedelta import relativedelta
import time
import json
import urllib
import hashlib
import core.const as const
import core.auxport as auxport
import core.internals as internals
import core.spotify_tools as spotify_tools
from logzero import logger as log
from flask import abort
from flask import request
import random
import threading
import loader
import uuid
import MySQLdb
import collections
import requests
import operator
from celery import Celery
from celery.task.control import revoke
from urllib.parse import unquote, quote
from werkzeug.datastructures import ImmutableOrderedMultiDict

class DB:
  conn = None

  def connect(self):
    self.conn = MySQLdb.connect(host="XXXXXXXXXXX", user="XXXXXXXXXXX", passwd="XXXXXXXXXXX", db="XXXXXXXXXXX")

  def query(self, sql, values):
    try:
      cursor = self.conn.cursor()
      cursor.execute(sql, values)
    except (AttributeError, MySQLdb.OperationalError):
      self.connect()
      cursor = self.conn.cursor()
      cursor.execute(sql, values)
    return cursor

# clone of write_tracks from spotify_tools.py
# returns tuple array of song name, artist, and uri
def get_tracks(tracks):
    trimmedTracks = []
    while True:
        log.info(tracks['items'])
        for item in tracks['items']:
            if 'track' in item:
                track = item['track']
            else:
                track = item
            try:
                track_name = track['name']
                track_artist = track['artists'][0]['name']
                track_url = track['external_urls']['spotify']
                trimmedTrack = (track_name, track_artist, track_url)
                trimmedTracks.append(trimmedTrack)
            except KeyError:
                log.warning(u'Skipping track {0} by {1} (local only?)'.format(
                    track['name'], track['artists'][0]['name']))
            except TypeError:
                log.warning(u'Track is null')
        # 1 page = 50 results
        # check if there are more pages
        if tracks['next']:
            tracks = spotify_tools.spotify.next(tracks)
        else:
            break
    return trimmedTracks

def get_time_left(seconds_left):
    m, s = divmod(seconds_left, 60)
    h, m = divmod(m, 60)
    seconds_string = "second" if (h == 1) else "seconds"
    minutes_string = "minute" if (m == 1) else "minutes"
    hours_string = "hour" if (h == 1) else "hours"
    time_left = ""
    if h == 0:
        if m == 0:
            time_left = "%d %s" % (s, seconds_string)
        else:
            time_left = "%d %s %d %s" % (m, minutes_string, s, seconds_string)
    else:
        time_left = "%d %s %d %s" % (h, hours_string, m, minutes_string)
    return time_left


loader.load_defaults()

app = Flask(__name__)

app.debug = True
app.config['CELERY_BROKER_URL'] = 'pyamqp://guest@localhost//'
app.config['SECRET_KEY'] = 'XXXXXXXXXXX'
app.config['PAYPAL_LIVE'] = True
max_converts = 300
mail_settings = {
    "MAIL_SERVER": 'XXXXXXXXXXX,
    "MAIL_PORT": 465,
    "MAIL_USE_TLS": False,
    "MAIL_USE_SSL": True,
    "MAIL_USERNAME": 'XXXXXXXXXXX',
    "MAIL_PASSWORD": 'XXXXXXXXXXX'
}
app.config.update(mail_settings)
mail = Mail(app)

PP_SCR_LIVE_URL = 'https://www.paypal.com/cgi-bin/webscr'
PP_SCR_SANDBOX_URL = 'https://www.sandbox.paypal.com/cgi-bin/webscr'
PP_SCR_URL = PP_SCR_LIVE_URL if app.config['PAYPAL_LIVE'] else PP_SCR_SANDBOX_URL
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)
db = DB()

class ServerError(Exception):pass

# Generate music and tmp folders if they don't exist yet
directories = ['music', 'hq_music', 'tmp']
for directory in directories:
    if not os.path.exists(directory):
        os.makedirs(directory)

currentTimeMillis = lambda: int(round(time.time() * 1000))
@celery.task(bind=True)
def convert(self, uri, convert_count, logged_in):
    self.playlist_uri = uri
    self.convert_count = convert_count
    self.logged_in = logged_in
    self.progress = 0
    self.done = 1
    self.out_of = 0
    self.time_left = ""
    self.current_song = ""
    self.error = ""
    self.finished = False
    tracks = []
    if 'playlist' in self.playlist_uri: # playlist
        try:
            playlist = spotify_tools.fetch_playlist(self.playlist_uri)
        except spotipy.client.SpotifyException as e:
            log.error(e)
            log.debug('Token expired, generating new one and authorizing')
            new_token = spotify_tools.generate_token()
            spotify_tools.spotify = spotipy.Spotify(auth=new_token)
            playlist = spotify_tools.fetch_playlist(self.playlist_uri)
        if playlist is None:
            self.error = "Could not find playlist. Please check the playlist URL and try again."
            return
        tracks = get_tracks(playlist['tracks'])
    else: # song
        try:
            meta_tags = spotify_tools.generate_metadata(self.playlist_uri)
        except spotipy.client.SpotifyException as e:
            log.error(e)
            self.error = "Could not find song. Please check the song URL and try again."
            return
        track_name = meta_tags['name']
        track_artist = meta_tags['artists'][0]['name']
        track_url = self.playlist_uri
        tracks.append((track_name, track_artist, track_url))
    self.progress = 10

    length = len(tracks)
    time_per_song = 9
    self.out_of = length
    seconds_left = length * time_per_song
    self.time_left = get_time_left(seconds_left)
    self.update_state(state="STARTED",
                     meta={'status': '200',
                           'error': self.error,
                           'finished': self.finished,
                           'progress': self.progress, 
                           'current_song': self.current_song,
                           'done': self.done,
                           'out_of': self.out_of,
                           'time_left': self.time_left})

    log.info(u'Preparing to convert {} songs'.format(length))
    percentPerSong = 90 / length

    converted_songs = []

    for number, track in enumerate(tracks, 1):
        embed_metadata = self.logged_in
        high_quality = self.logged_in
        if self.logged_in or self.convert_count <= max_converts:
            track_name = track[0]
            track_artist = track[1]
            track_url = track[2]
            start_time = time.time()
            self.current_song = internals.sanitize_title('{} - {}'.format(track_name, track_artist))

            # either not yet converted or not fully converted
                try:
                    auxport.convert_single(track_url, folder, number, embed_metadata, high_quality)
                    time_per_song = (time.time() - start_time)
                    self.convert_count += 1
                # token expires after 1 hour
                except spotipy.client.SpotifyException as e:
                    # refresh token when it expires
                    log.error(e)
                    log.debug('Token expired, generating new one and authorizing')
                    new_token = spotify_tools.generate_token()
                    spotify_tools.spotify = spotipy.Spotify(auth=new_token)
                    auxport.convert_single(track_url, folder, number, embed_metadata, high_quality)
                    time_per_song = (time.time() - start_time)
                    self.convert_count += 1
                # detect network problems
                except (urllib.request.URLError, TypeError, IOError) as e:
                    log.error(e)
                    log.debug('Network error when converting {} by {}'.format(track_name, track_artist))
                    continue
                except Exception as e:
                    log.error(e)
                    continue
            else:
                self.convert_count += 1
            seconds_left = (length - self.done) * time_per_song
            if self.done < self.out_of:
                self.done += 1
            self.progress += percentPerSong
            self.time_left = get_time_left(seconds_left)
            self.update_state(state="PROGRESS",
                    meta={'status': '200',
                        'error': self.error,
                        'finished': self.finished,
                        'progress': self.progress, 
                        'current_song': self.current_song,
                        'done': self.done,
                        'out_of': self.out_of,
                        'time_left': self.time_left})
            log.debug('Percent:' + str(self.progress) + "%")
        else: # limit reached
            break
    self.progress = 100
    self.update_state(state="PROGRESS",
                     meta={'status': '200',
                           'error': self.error,
                           'finished': self.finished,
                           'progress': self.progress, 
                           'current_song': self.current_song,
                           'done': self.done,
                           'out_of': self.out_of,
                           'time_left': self.time_left})
    self.finished = True
    self.update_state(state="SUCCESS",
                     meta={'status': '200',
                           'error': self.error,
                           'finished': self.finished,
                           'progress': self.progress, 
                           'current_song': self.current_song,
                           'done': self.done,
                           'out_of': self.out_of,
                           'time_left': self.time_left})

def after_this_request(func):
    if not hasattr(g, 'call_after_request'):
        g.call_after_request = []
    g.call_after_request.append(func)
    return func

def createPaypalQuery(email, password, plan):
    ip_addr = request.environ['REMOTE_ADDR'] if request.environ.get('HTTP_X_FORWARDED_FOR') is None else request.environ['HTTP_X_FORWARDED_FOR']
    response = urllib.request.urlopen('http://www.geoplugin.net/json.gp?ip={}'.format(ip_addr))
    data = json.load(response)
    continent = data['geoplugin_continentCode']
    base_url = "https://www.paypal.com/cgi-bin/webscr"
    query = collections.OrderedDict()
    query['on0'] = "email"
    query['os0'] = email
    query['on1'] = "hash"
    query['os1'] = password
    query_string = urllib.parse.urlencode(query)
    return base_url + "?" + query_string

@app.after_request
def per_request_callbacks(response):
    for func in getattr(g, 'call_after_request', ()):
        response = func(response)
    return response

@app.route('/')
def index():
    ip_addr = request.environ['REMOTE_ADDR'] if request.environ.get('HTTP_X_FORWARDED_FOR') is None else request.environ['HTTP_X_FORWARDED_FOR']
    response = urllib.request.urlopen('http://www.geoplugin.net/json.gp?ip={}'.format(ip_addr))
    data = json.load(response)
    continent = data['geoplugin_continentCode']
    logged_in = 'email' in session
    return render_template("home.html", logged_in=logged_in, continent=continent)

@app.route('/init', methods=['POST'])
def convert_song():
    if not 'converts' in session:
        session['converts'] = 0
    logged_in = 'email' in session
    if session['converts'] <= max_converts or logged_in:
        playlist_uri = json.loads(request.data)['playlist_uri']
        result = tasks.get_playlist.apply_async(args=(playlist_uri,), queue='tasks', priority=1)
        return json.dumps({'status': '200', 'task_id': str(result.task_id)})
    else:
        return json.dumps({'status': '403'})
    else:
        return json.dumps({'status': '503'})

@app.route('/get_playlist', methods=['POST'])
def fetch_playlist():
    task_id = json.loads(request.data)['task_id']
    if task_id:
        async_result = AsyncResult(task_id)
        finished = async_result.ready()
        if finished:
            return json.dumps({'status': '200', 'ready': True, 'songs': async_result.result})
    return json.dumps({'status': '200', 'ready': False})

@app.route('/convert', methods=['POST'])
def dl_song():
    logged_in = 'email' in session
    if session['converts'] <= max_converts or logged_in:
        song_name = json.loads(request.data)['song_name']
        song_uri = json.loads(request.data)['song_uri']
        result = tasks.convert_song.apply_async(args=(song_uri, song_name, logged_in), queue='tasks', priority=3)
        return json.dumps({'status': '200', 'task_id': str(result.task_id)})
    else:
        return json.dumps({'status': '403'})
    
@app.route('/progress', methods=['POST'])
def get_progress():
    thread_id = json.loads(request.data)['thread_id']
    logged_in = 'email' in session

    if task_id:
        async_result = AsyncResult(task_id)
        response = async_result.result
        finished = async_result.ready()
        if finished:
            return json.dumps({'status': '200', 'ready': True, 'time_elapsed': str(response[1])})
    return json.dumps({'status': '200', 'ready': False})

@app.route('/stop', methods=['POST'])
def on_disconnect():
    thread_id = json.loads(request.data)['task_id']
    stop_thread(thread_id)
    return ('', 204)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        if 'email' in session:
            return json.dumps({'status': '200'})

        error = None
        # try:
        email_form  = json.loads(request.data)['email']
        password_form  = json.loads(request.data)['password']
        plan_form = json.loads(request.data)['plan']
        cur = db.query("SELECT COUNT(1) FROM users WHERE email = %s;", (email_form,))
        if cur.fetchone()[0]:
            return json.dumps({'status': '400', 'message': 'An account with that email already exists.'})

        # encrypt password
        password_form = hashlib.md5(bytes(password_form, "ascii")).hexdigest()

        return json.dumps({'status': '200', 'message': 'Redirecting to payment...', 'url': createPaypalQuery(email_form, password_form, plan_form)})
    else:
        return render_template("home.html", action="sign_up")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'email' in session:
        return redirect(url_for('index'))

    error = None
    try:
        if request.method == 'POST':
            email_form  = json.loads(request.data)['email']
            date = datetime.datetime.today().strftime('%Y-%m-%d')
            cur = db.query("SELECT COUNT(1) FROM users WHERE email = %s AND expiry >= %s;", (email_form, date,))

            if not cur.fetchone()[0]:
                raise ServerError('Invalid username')

            password_form  = json.loads(request.data)['password']
            cur = db.query("SELECT password FROM users WHERE email = %s;", (email_form,))

            for row in cur.fetchall():
                if hashlib.md5(bytes(password_form, "ascii")).hexdigest() == row[0]:
                    session.permanent = True
                    session['email'] = json.loads(request.data)['email']
                    return json.dumps({'status': '200', 'message': 'Logged in successfully! Redirecting...'})

            raise ServerError('Invalid password')
        else:
            # redirect to index and show login modal
            return render_template("home.html", action="log_in")
    except ServerError as e:
        error = str(e)
        return json.dumps({'status': '400', 'message': error})

    return json.dumps({'status': '200', 'message': 'Logged in successfully! Redirecting...'})

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.pop('email', None)
    return redirect(url_for('index'))

@app.route('/ipn', methods=['POST'])
def ipn():
    arg = ''
    values = request.form
    txn_type = request.form.get('txn_type')
    mc_gross = request.form.get('mc_gross')
    log.info(values)

    # for x, y in values.items():
    #     arg += "&{x}={y}".format(x=x,y=y)

    # validate_url = PP_SCR_URL + '?cmd=_notify-validate{arg}'.format(arg=urllib.parse.quote(arg))
    # r = requests.get(validate_url)
    # log.info(validate_url + ' returned ' + r.text)
    if txn_type == "web_accept" or txn_type == "subscr_payment" or txn_type == "subscr_signup":
        date = datetime.datetime.today().strftime('%Y-%m-%d')

        email = request.form.get('option_selection1')
        first_name = request.form.get('first_name')
        plan =  request.form.get('item_name')
        payer_id = request.form.get('payer_id')
        email = request.form.get('option_selection1')
        password =  request.form.get('option_selection2')
        email = email if "@" in email else request.form.get('option_selection2')
        password = password if "@" in email else request.form.get('option_selection1')
        plan_name = ''
        date = datetime.datetime.today().strftime('%Y-%m-%d')

        cur = db.query("SELECT COUNT(1) FROM users WHERE email = %s;", (email,))

        # Send email if new account
        if not cur.fetchone()[0]:
            message = '''<img src="https://auxport.com/static/img/logo_dark.png"/>
                <p>Hey {},</p>
                <p>Thank you for signing up for Auxport Premium. Your account has been created with the following email:</p>

                <p>{}</p>

                <p>You can now log in to your account using the following link below:</p>
                <a href="https://auxport.com/login">https://auxport.com/login</a>
                
                <p>Thank you for your support,</p>
                <p>The Auxport Team</p>
                '''.format(first_name, email)

            msg = Message(subject="Welcome to Auxport",
                        sender=app.config.get("MAIL_USERNAME"),
                        recipients=[email],
                        html=message)
            mail.send(msg)
    
        db.query("INSERT INTO users (email, password, plan, payer_id, expiry, pwd_token) VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE expiry=%s;", (email, password, plan_name, payer_id, date, '', date,))
        session['email'] = email
    elif mc_gross != None and int(mc_gross) < 0:
        # delete and refund
        payer_id = request.form.get('payer_id')
        db.query("DELETE FROM users WHERE payer_id=%s;", (payer_id))
    # else:
    #     log.info('Paypal IPN string {arg} did not validate'.format(arg=arg))

    return json.dumps({'status': '200'})

def randomCalc():
    ops = {'+':operator.add,
           '-':operator.sub}
    num1 = random.randint(1,10)
    num2 = random.randint(1,10)
    op = random.choice(list(ops.keys()))
    while op == '-' and num1 <= num2:
        num1 = random.randint(1,10)
        num2 = random.randint(1,10)
    answer = ops.get(op)(num1,num2)
    return ('What is {} {} {}?\n'.format(num1, op, num2), answer)

@app.route('/contact')
def contact():
    logged_in = 'email' in session
    random_problem = randomCalc()
    random_question = random_problem[0]
    random_answer = random_problem[1]
    return render_template("contact.html", logged_in=logged_in, question=random_question, answer=random_answer)

@app.route('/send_message', methods=['POST'])
def send_email():
    subject = json.loads(request.data)['subject']
    email = json.loads(request.data)['email']
    message = json.loads(request.data)['message']
    if subject != "" and email != "" and message != "":
        msg = Message(subject=subject,
                      sender=app.config.get("MAIL_USERNAME"),
                      reply_to=email,
                      recipients=["noreply@auxport.com"],
                      body=message)

        mail.send(msg)
        return json.dumps({'status': '200'})
    else:
        return json.dumps({'status': '403'})
    return json.dumps({'status': '500'})

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_pwd():    
    pwd_code = request.args.get('key', '')
    if request.method == 'POST':
        post_data = json.loads(request.data)

        if "pwd_token" in post_data and "password" in post_data:
            # reset password and empty password reset token
            new_pwd = json.loads(request.data)['password']
            pwd_token = json.loads(request.data)['pwd_token']

            # encrypt password
            new_pwd = hashlib.md5(bytes(new_pwd, "ascii")).hexdigest()

            cur = db.query("SELECT COUNT(1) FROM users WHERE pwd_token = %s;", (pwd_token,))
            if cur.fetchone()[0]:
                # reset password
                db.query("UPDATE users SET password=%s, pwd_token='' WHERE pwd_token=%s;", (new_pwd, pwd_token))
                return json.dumps({'status': '200', 'message': 'Your password was reset successfully!'})
            else:
                # token expired
                return json.dumps({'status': '400', 'message': 'Your password reset token has expired, please request a new one'})
        else:
            if "email" in post_data:
                email = json.loads(request.data)['email']
                # check if account exists
                cur = db.query("SELECT COUNT(1) FROM users WHERE email = %s;", (email,))
                if cur.fetchone()[0]:
                    # if account exists
                    # generate reset code
                    pwd_token = uuid.uuid4().hex

                    # add to account
                    db.query("UPDATE users SET pwd_token=%s WHERE email=%s;", (pwd_token, email,))

                    # send email
                    email_hash = hashlib.md5(bytes(email, "ascii")).hexdigest()
                    message = '''<img src="https://auxport.com/static/img/logo_dark.png"/>
                    <p>Hello,</p>
                    <p>You recently requested to reset your password. You can set a new password by clicking the link below.</p>
                    
                    <a href="https://auxport.com/reset_password?key={}">https://auxport.com/reset_password?key={}</a>
                    
                    <p>If you didn't request a password reset, you can ignore this email.</p>
                    
                    <p>The Auxport Team</p>
                    '''.format(pwd_token, pwd_token)

                    msg = Message(subject="Reset your Auxport Password",
                                sender=app.config.get("MAIL_USERNAME"),
                                recipients=[email],
                                html=message)
                    mail.send(msg)
                    return json.dumps({'status': '200'})
    elif request.method == 'GET':
        if pwd_code != None:
            # reset password
            log.info(pwd_code)
            return render_template("home.html", pwd_token=pwd_code)
    return json.dumps({'status': '403'})

@app.route('/privacy-policy')
def privacy():
    logged_in = 'email' in session
    return render_template("privacy-policy.html", logged_in=logged_in)

@app.route('/terms-of-use')
def terms():
    logged_in = 'email' in session
    return render_template("terms-of-use.html", logged_in=logged_in)

@app.route('/sitemap.xml')
def static_from_root():
    return send_from_directory(app.static_folder, request.path[1:])

if __name__ == '__main__':
    app.run(host='0.0.0.0')
