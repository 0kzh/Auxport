from logzero import logging
from logzero import logger as log
import os
import time
import core.spotify_tools as spotify_tools
import core.const as const
import core.internals as internals
import core.converter as converter
from core.converter import converter
import spotipy
import spotipy.oauth2 as oauth2
import urllib

from celery import Celery

app = Celery()
app.config_from_object('celeryconfig')

# clone of write_tracks from spotify_tools.py
# returns tuple array of song name, artist, and uri
def get_tracks(tracks):
    trimmedTracks = []
    while True:
        for item in tracks['items']:
            if 'track' in item:
                track = item['track']
            else:
                track = item
            try:
                track_name = track['name']
                track_artist = track['artists'][0]['name']
                track_url = track['external_urls']['spotify']
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

@app.task
def get_playlist(playlist_uri):
    tracks = []
    if 'playlist' in playlist_uri: # playlist
        try:
            playlist = spotify_tools.fetch_playlist(playlist_uri)
        except spotipy.client.SpotifyException as e:
            log.error(e)
            log.debug('Token expired, generating new one and authorizing')
            new_token = spotify_tools.generate_token()
            spotify_tools.spotify = spotipy.Spotify(auth=new_token)
            playlist = spotify_tools.fetch_playlist(playlist_uri)
        if playlist is None:    
            # self.error = "Could not find playlist. Please check the playlist URL and try again."
            return
        tracks = get_tracks(playlist['tracks'])
    else: # song
        try:
            meta_tags = spotify_tools.generate_metadata(playlist_uri)
        except spotipy.client.SpotifyException as e:
            log.error(e)
            # self.error = "Could not find song. Please check the song URL and try again."
            return
        track_name = meta_tags['name']
        track_artist = meta_tags['artists'][0]['name']
        track_url = playlist_uri
    return tracks