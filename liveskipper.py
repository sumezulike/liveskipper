# pylint: disable=logging-fstring-interpolation
"""LiveSkipper, a script to automatically skip live versions on spotify."""
import collections
import logging
import re
import sys
import time

import spotipy  # type: ignore[import]
import spotipy.oauth2  # type: ignore[import]
import musicbrainzngs  # type: ignore[import]

from musicbrainzngs import ResponseError

import config


def start_local_http_server_wildcard_ip(port, handler=spotipy.oauth2.RequestHandler):
    """Monkeypatching to allow the auth request into the docker container."""
    server = spotipy.oauth2.HTTPServer(("0.0.0.0", port), handler)
    server.allow_reuse_address = True
    server.auth_code = None
    server.auth_token_form = None
    server.error = None
    return server


spotipy.oauth2.start_local_http_server = start_local_http_server_wildcard_ip

logger = logging.getLogger("LiveGuard")
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.INFO)

musicbrainzngs.set_useragent("LiveSkipper", "0.1", config.EMAIL_ADDRESS)

scope = "user-modify-playback-state", "user-read-currently-playing", "user-library-read"

logger.info("Getting Spotify auth")

auth = spotipy.oauth2.SpotifyOAuth(
    scope=scope,
    client_id=config.CLIENT_ID,
    client_secret=config.CLIENT_SECRET,
    redirect_uri=config.SPOTIFY_REDIRECT_URL,
    open_browser=True,
)

logger.info(f"Go to the following URL: {auth.get_authorize_url()}")

sp = spotipy.Spotify(
    auth_manager=auth,
)

user = sp.me()
logger.info(f"Authorized by {user['display_name']}.")


class UnsureError(Exception):
    """Raised when no decision could be made about whether a song is a live version"""

    def __init__(self, *args):
        super().__init__(*args)


def dates_fit(date1: str, date2: str, allow_fail: bool = True) -> bool:
    """Compare two date strings.
    Arguments:
         allow_fail: Whether to return True or False when the passed strings can't be interpreted.

    """

    def get_year(date: str) -> int:
        if len(date) != 4:
            year_match = re.findall(r"\d{4}", date)
            if len(year_match) == 1:
                date = year_match[0]
        try:
            year = int(date)
        except ValueError:
            logger.debug(f"Weird date: {date}")
            year = 0
        return year

    year1 = get_year(date1)
    year2 = get_year(date2)

    if year1 == 0 or year2 == 0:
        return allow_fail
    return year1 == year2


class LiveSkipper:
    """The main class."""

    def __init__(self) -> None:
        self.excepted_tracks: list[str] = []
        self.previous_skips: collections.deque[str] = collections.deque([], maxlen=5)
        self.prev_track_id = None

    def is_excepted(self, track: dict) -> bool:
        """Check whether a track is in the excepted tracks."""
        return track["item"]["id"] in self.excepted_tracks

    def is_live_by_isrc(self, track: dict) -> bool:
        """
        Query musicbrainz with the tracks ISRC to decide whether it is live.
        Raises:
             UnsureError if no decision could be made or the ISRC was not found.
        """
        isrc = track["item"]["external_ids"]["isrc"]
        try:
            track_info = musicbrainzngs.get_recordings_by_isrc(isrc)
            logger.debug(track_info)
            disambiguations: list[str] = list(
                filter(
                    None,
                    [
                        t.get("disambiguation", "")
                        for t in track_info["isrc"]["recording-list"]
                    ],
                )
            )
            logger.debug(disambiguations)
            if not disambiguations:
                raise UnsureError("No info in ISRC results")
            return any(d.startswith("live") for d in disambiguations)
        except ResponseError as ex:
            raise UnsureError(f"ISRC {isrc} not found") from ex

    def is_live_by_release(self, track: dict) -> bool:
        """
        Query musicbrainz with the tracks album name, artist, and release year
        to decide whether it is live.
        Raises:
             UnsureError if no decision could be made or no fitting releases were found.
        """
        artist = track["item"]["album"]["artists"][0]["name"]
        album_title = track["item"]["album"]["name"]
        release_date = track["item"]["album"]["release_date"]
        try:
            release_info = musicbrainzngs.search_releases(
                f"artist:{artist} AND release:{album_title}"
            )
            fitting = [
                r
                for r in release_info["release-list"]
                if artist.lower()
                in [r["artist-credit"][0]["name"].lower(), r["artist-credit-phrase"]]
                and (
                    r["title"].lower() in album_title.lower()
                    or album_title.lower() in r["title"].lower()
                )
                and dates_fit(r.get("date", "0"), release_date)
                and r["release-group"]["type"].lower() != "compilation"
            ]
            logger.debug(fitting)
            if fitting:
                live_vote = sum(
                    r["release-group"]["type"].lower() == "live" for r in fitting
                )
                logger.info(f"{len(fitting)} releases, {live_vote} vote live.")
                return live_vote >= len(fitting) / 2

            raise UnsureError("No fitting releases found.")

        except ResponseError as ex:
            raise UnsureError(f"Release {artist} - {album_title} not found.") from ex

    def is_live_by_track(self, track: dict) -> bool:
        """
        Query musicbrainz with the tracks name and artist to decide whether it is live.
        Raises:
             UnsureError if no decision could be made or no fitting tracks were found.
        """
        artist = track["item"]["album"]["artists"][0]["name"]
        track_title = track["item"]["name"]

        try:
            track_info = musicbrainzngs.search_recordings(
                f"artist:{artist} AND recording:{track_title}"
            )
            fitting = [
                t
                for t in track_info["recording-list"]
                if artist.lower()
                in [
                    t["artist-credit"][0]["name"].lower(),
                    t["artist-credit-phrase"].lower(),
                ]
                and (
                    t["title"].lower() in track_title.lower()
                    or track_title.lower() in t["title"].lower()
                )
            ]
            logger.debug(fitting)
            if not fitting:
                raise UnsureError("No fitting tracks found.")
            disambiguations: list[str] = list(
                filter(
                    None,
                    [t.get("disambiguation", "") for t in fitting],
                )
            )
            if disambiguations:
                live_vote = sum(d.startswith("live") for d in disambiguations)
                logger.info(f"{len(disambiguations)} tracks, {live_vote} vote live.")
                return live_vote >= len(disambiguations) / 2

            raise UnsureError("No info in tracks.")
        except ResponseError as ex:
            raise UnsureError(f"Track {artist} - {track_title} not found.") from ex

    def is_live(self, track: dict) -> bool:
        """Checks whether a song is a live version.
        Returns False if no decision could be made.
        """
        try:
            logger.debug("Attempting ISRC search")
            result = self.is_live_by_isrc(track)
            logger.info(f"Live: {result} (ISRC)")
            return result
        except UnsureError as ex:
            logger.warning(ex)
            logger.warning("Falling back to album search")

        try:
            logger.debug("Attempting release search")
            result = self.is_live_by_release(track)
            logger.info(f"Live: {result} (release)")
            return result
        except UnsureError as ex:
            logger.warning(ex)
            logger.warning("Falling back to track search")

        try:
            logger.debug("Attempting track search")
            result = self.is_live_by_track(track)
            logger.info(f"Live: {result} (recording)")
            return result
        except UnsureError as ex:
            logger.warning(ex)
            logger.warning("Giving up.")

        return False

    def check(self) -> bool:
        """Get the currently playing song and skip it, if necessary.
        Return indicates whether a song was skipped.
        """
        current_track = sp.current_user_playing_track()
        if (
            not current_track
            or not current_track["item"]
            or not current_track["is_playing"]
            or current_track["item"]["id"] == self.prev_track_id
        ):
            return False

        logger.info(
            f"\nNow listening to {current_track['item']['artists'][0]['name']} - "
            f"{current_track['item']['name']}"
        )
        self.prev_track_id = current_track["item"]["id"]

        if self.is_excepted(current_track):
            return False

        if not self.is_live(current_track):
            return False

        if sp.current_user_saved_tracks_contains([current_track["item"]["id"]])[0]:
            logger.info("Found in saved tracks.")
            logger.info(
                f"Adding {current_track['item']['artists'][0]['name']} - "
                f"{current_track['item']['name']} to exceptions"
            )
            self.excepted_tracks.append(current_track["item"]["id"])
            return False

        if current_track["item"]["id"] in self.previous_skips:
            logger.info("Registered replay.")
            logger.info(
                f"Adding {current_track['item']['artists'][0]['name']} - "
                f"{current_track['item']['name']} to exceptions"
            )
            self.excepted_tracks.append(current_track["item"]["id"])
            return False

        logger.info(
            f"Skipping {current_track['item']['artists'][0]['name']} - "
            f"{current_track['item']['name']}"
        )
        try:
            sp.next_track()
        except Exception as ex:
            logger.error("Skipping failed.")
            raise ex

        self.previous_skips.appendleft(current_track["item"]["id"])
        return True

    def run_forever(self):
        """Run the LiveSkipper in an endless loop, ignoring exceptions."""
        logger.info("LiveSkipper active.")
        while True:
            try:
                self.check()
            except spotipy.oauth2.SpotifyOauthError as ex:
                logger.error(f"Spotify OAuthError: {ex}. Exiting.")
                sys.exit(-1)
            except Exception as ex:  # pylint:disable=broad-exception-caught
                logger.error(f"Ignoring unexpected exception: {ex}")
            time.sleep(3)


if __name__ == "__main__":
    LiveSkipper().run_forever()
