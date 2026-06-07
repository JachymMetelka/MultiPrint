# Multi3D

Spuštění za pomoci příkazů

docker stop multiprint-app
docker rm multiprint-app
docker build --no-cache -t multiprint-is .
docker run -d -p 8000:8000 -v "${PWD}:/app" --name multiprint-app multiprint-is
