# Multi3D

Spuštění za pomoci příkazů

docker stop multiprint-app </br>
docker rm multiprint-app </br>
docker build --no-cache -t multiprint-is . </br>
docker run -d -p 8000:8000 -v "${PWD}:/app" --name multiprint-app multiprint-is
