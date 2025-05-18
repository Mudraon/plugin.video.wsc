import requests
import xbmc
import xbmcgui
import re
import io
import os
import json

FOLDER_NAME = "series_db_tmdb"

class TMDB:
    def __init__(self, addon, profile):
        self.addon = addon
        self.profile = profile
        self.API_TOKEN = addon.getSetting('tmdb_token')
        self.LANG = addon.getSetting('tmdb_lang')
        self.series_db_path = os.path.join(profile, FOLDER_NAME)
        self.ensure_db_exists()

    def ensure_db_exists(self):
        """Ensure that the series database directory exists"""
        try:
            if not os.path.exists(self.profile):
                os.makedirs(self.profile)
            if not os.path.exists(self.series_db_path):
                os.makedirs(self.series_db_path)
        except Exception as e:
            xbmc.log(f'WebshareCinema: Error creating directories: {str(e)}', level=xbmc.LOGERROR)

    def FindSeries(self, series_name):
        results = self.get_series_info(series_name)
        selected = self.choose_series_from_results(results)
        #seasons = get_series_details(selected['id'], API_TOKEN, LANG)
        #details = get_season_episodes(selected['id'], 1, API_TOKEN, LANG)
        if selected:
            return selected
        return None

    def get_series_info(self, series_name):
        url = "https://api.themoviedb.org/3/search/tv"
        params = {
            "api_key": self.API_TOKEN,
            "query": series_name,
            "language": self.LANG,
            "include_adult": "false"
        }

        response = requests.get(url, params=params)
        if response.status_code != 200:
            return None

        data = response.json()
        #xbmc.log(f"get_series_info: {data}", xbmc.LOGINFO)
        return data.get("results", [])

    def get_series_details(self, series_id):
        """Získá detailní info o seriálu včetně počtu sezón."""
        url = f"https://api.themoviedb.org/3/tv/{series_id}"
        params = {
            "api_key": self.API_TOKEN,
            "language": self.LANG
        }

        response = requests.get(url, params=params)
        if response.status_code != 200:
            xbmc.log(f"Chyba při načítání detailu seriálu (status {response.status_code})", xbmc.LOGERROR)
            return None
        data = response.json()
        #return data
        #xbmc.log(f"get_series_details: {data}", xbmc.LOGINFO)
        return data.get("seasons")

    def get_season_episodes(self, series_id, season_number):
        """Získá seznam epizod pro danou sezónu."""
        url = f"https://api.themoviedb.org/3/tv/{series_id}/season/{season_number}"
        params = {
            "api_key": self.API_TOKEN,
            "language": self.LANG
        }

        response = requests.get(url, params=params)
        if response.status_code != 200:
            xbmc.log(f"Chyba při načítání sezóny {season_number} (status {response.status_code})", xbmc.LOGERROR)
            return []

        data = response.json()
        #return data
        #xbmc.log(f"get_season_episodes: {data}", xbmc.LOGINFO)
        return data.get('episodes', [])

    def choose_series_from_results(self, results):
        if not results:
            xbmcgui.Dialog().notification("TMDb", "Nebyly nalezeny žádné výsledky", xbmcgui.NOTIFICATION_ERROR)
            return None
        
        options = []
        for item in results:
            year = item.get('first_air_date', '')[:4] if item.get('first_air_date') else ''
            title = item.get('name', 'Unknown')
            display_name = f"{title} ({year})" if year else title
            options.append(display_name)
        
        dialog = xbmcgui.Dialog()
        selected_index = dialog.select("Vyber správnou variantu", options)
        
        if selected_index == -1:
            return None
        
        return results[selected_index]

    def build_tmdb_series_structure(self, selected, seasons):
        series_data = {
            "name": selected.get("name", "Unknown"),
            "original_name": selected.get("original_name", "Unknown"),
            "id": selected["id"],
            "seasons": {}
        }

        for season in seasons:
            season_number = season.get("season_number")
            season_name = season.get("name", f"Sezóna {season_number}")
            if season_number == 0:
                continue  # přeskočí speciály   
            
            # Načti epizody pro danou sezónu
            episodes = self.get_season_episodes(selected["id"], season_number)
            if not episodes:
                continue

            season_dict = {}
            for ep in episodes:
                ep_name = ep.get("name", f"Epizoda {ep.get('episode_number')}")
                season_dict[ep_name] = {}  # zatím prázdné – sem později dáme streamy

            series_data["seasons"][season_name] = season_dict

        return series_data

def save_series_structure(series_data, folder_path):
    safe_name = re.sub(r'[^\w\-_\. ]', '_', series_data["original_name"]).lower().replace(" ", "_")
    file_path = os.path.join(folder_path, f"{safe_name}.json")
    try:
        with io.open(file_path, 'w', encoding='utf8') as file:
            try:
                data = json.dumps(series_data, indent=2).decode('utf8')
            except AttributeError:
                data = json.dumps(series_data, indent=2)
            file.write(data)
            file.close()
    except Exception as e:
        xbmc.log(f'WebshareCinema: Error saving series data: {str(e)}', level=xbmc.LOGERROR)