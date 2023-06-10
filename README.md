# LiveSkipper

Skips those pesky live versions that Spotify tries to jam down your ears while you're trying to enjoy some crisp high-quality studio-recorded music without disturbing crowd noises.

### Requirements
A Spotify developer account so you can create an app and get credentials
https://developer.spotify.com/dashboard/create

Set the redirect url to http://localhost:9090.

### Usage
Clone the repository and enter your credentials into `config.py`.

Then just run
```
docker-compose up
```

Alternatively if you don't want to use docker, run 
```
cd liveskipper
pip install -r requirements.txt
python liveskipper.py
```


I admit there are some very few good live recordings, so your saved songs will not be skipped. 

Also, if a song is skipped and you actually wanted to listen to it, just restart it and it won't be skipped again.
