from app.api.zenavia_api import ZenaviaAPI

api = ZenaviaAPI()
maps = api.get_maps()

print("Nombre de maps :", len(maps))
for m in maps[:10]:
    print(m)