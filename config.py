"""Config for LiveSkipper"""
# Spotify App Client ID
CLIENT_ID = "your-client-id"

# Spotify App Client Secret
CLIENT_SECRET = "your-client-secret"

# Spotify Auth Redirect URL
# I strongly recommend not to change this
SPOTIFY_REDIRECT_URL = "http://localhost:9090"

# Your Email Address
# In theory musicbrainz will use this to contact you if you overdo it
# but rate limit is 50 requests/second and we make 3 requests every 3 seconds at max
# so that won't happen. Still fill this out, please.
EMAIL_ADDRESS = "your-email-address"
