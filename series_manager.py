# -*- coding: utf-8 -*-
# Module: series_manager
# Author: user extension
# Created on: 5.6.2023
# License: AGPL v.3 https://www.gnu.org/licenses/agpl-3.0.html

import os
import io
import re
import json
import xbmc
import xbmcaddon
import xbmcgui
import xml.etree.ElementTree as ET
import themoviedb

try:
    from urllib import urlencode
    from urlparse import parse_qsl
except ImportError:
    from urllib.parse import urlencode
    from urllib.parse import parse_qsl

try:
    from xbmc import translatePath
except ImportError:
    from xbmcvfs import translatePath

# Regular expressions for detecting episode patterns
EPISODE_PATTERNS = [
    r'[Ss](\d+)[xX][Ee](\d+)',     # S01xE01, S01XE01 (např. "S06xE02")
    r'[Ss](\d+)[Ee](\d+)',         # S01E01
    r'(\d+)[xX](\d+)',             # 1x01
    r'[Ss]eason[\s._-]*(\d+)[\s._-]*Episode[\s._-]*(\d+)',  # Season 1 Episode 2
    r'[Ee]pisode[\s._-]*(\d+)',    # Episode 12
    r'[Ee]p[\s._-]*(\d+)',         # Ep 12
    r'[Ee](\d+)',                  # E12 (pozor, může být příliš obecné)
    r'(\d{1,2})\.(\d{2})',         # 1.01 nebo 10.03
    r'\[(\d+)x(\d+)\]',            # [3x06]
    r'\(s\s*(\d+)\s*e\s*(\d+)\)',  # (s8 e1) nebo (s 8 e 1)
    r'[sS](\d+)\s?[eE](\d+)',      # s2 e1 nebo s 2 e 1
]

class SeriesManager:
    def __init__(self, addon, profile):
        self.addon = addon
        self.profile = profile
        self.series_db_path = os.path.join(profile, 'series_db')
        self.ensure_db_exists()

    def delete_series(self, series_name):
        filename = series_name
        filepath = os.path.join(self.series_db_path , filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            xbmc.log(f"[PLUGIN] Seriál '{series_name}' smazán ({filepath})", xbmc.LOGINFO)
        else:
            xbmc.log(f"[PLUGIN] Soubor nenalezen pro smazání: {filepath}", xbmc.LOGWARNING)

    def ensure_db_exists(self):
        """Ensure that the series database directory exists"""
        try:
            if not os.path.exists(self.profile):
                os.makedirs(self.profile)
            if not os.path.exists(self.series_db_path):
                os.makedirs(self.series_db_path)
        except Exception as e:
            xbmc.log(f'WebshareCinema: Error creating directories: {str(e)}', level=xbmc.LOGERROR)

    def normalize_series_name(self, name):
    # Odstraní extra mezery, převede na lowercase
        name = name.strip().lower()
        # Nahraď mezery různými oddělovači (tečka, podtržítko, pomlčka)
        variants = [
            name,
            re.sub(r'\s+', '.', name),       # how.i.met.your.mother
            re.sub(r'\s+', '_', name),       # how_i_met_your_mother
            re.sub(r'\s+', '-', name),       # how-i-met-your-mother
            re.sub(r'\s+', '', name),        # howimetyourmother (bez mezer)
        ]
        return list(dict.fromkeys(variants))  # odstraní duplicitní hodnoty

    def build_fuzzy_name_queries(self, series_name):
        normalized = self.normalize_series_name(series_name)
        queries = []

        for name in normalized:
            queries.append(name)                                # e.g. how i met your mother
            queries.append(f"{name} season")                    # e.g. how i met your mother season
            queries.append(f"{name} episode")                   # e.g. how i met your mother episode
            queries.append(f"{name} tv show")                   # e.g. how i met your mother tv show
            queries.append(f"{name} full series")               # e.g. how i met your mother full series
            queries.append(f"{name} s01")                       # e.g. how i met your mother s01
            queries.append(f"{name} season 1")                  # e.g. how i met your mother season 1
        
        return queries
    
    def search_series(self, series_name, api_function, token):
        """Search for episodes of a series"""
        # Structure to hold results
        series_data = {
            'name': series_name,
            'last_updated': xbmc.getInfoLabel('System.Date'),
            'seasons': {}
        }

        # Build improved search queries
        search_queries = self.build_fuzzy_name_queries(series_name)
        all_results = []

        # 1. Search with diacritics
        for query in search_queries:
            results = self._perform_search(query, api_function, token)
            for result in results:
                result['_query'] = query
                if result not in all_results and self._is_likely_episode(result['name'], query):
                    all_results.append(result)

        # 2. Search without diacritics
        series_name_without_diacritics = self.remove_diacritics(series_name)
        if series_name_without_diacritics != series_name:  # Only if there were diacritics
            search_queries_without_diacritics = self.build_fuzzy_name_queries(series_name_without_diacritics)
            for query in search_queries_without_diacritics:
                results = self._perform_search(query, api_function, token)
                for result in results:
                    result['_query'] = query
                    if result not in all_results and self._is_likely_episode(result['name'], query):
                        all_results.append(result)

        # Process results and organize into seasons and episodes
        for item in all_results:
            query = item.get('_query', series_name)
            season_num, episode_num = self._detect_episode_info(item['name'], item['_query'])
            if season_num is not None:
                season_num_str = str(season_num)
                episode_num_str = str(episode_num)

                # Organize by season and episode (without worrying about file format)
                if season_num_str not in series_data['seasons']:
                    series_data['seasons'][season_num_str] = {}

                if episode_num_str not in series_data['seasons'][season_num_str]:
                    series_data['seasons'][season_num_str][episode_num_str] = []

                # Add the result to the corresponding episode
                series_data['seasons'][season_num_str][episode_num_str].append({
                    'name': item['name'],
                    'ident': item['ident'],
                    'size': item.get('size', '0')
                })

        # Save the series data
        self._save_series_data(series_name, series_data)

        return series_data
    
    def _is_likely_episode(self, filename, series_name):
        """Check if a filename is likely to be an episode of the series"""
        # Skip if doesn't contain series name
        if not re.search(re.escape(series_name), filename, re.IGNORECASE):
            return False
            
        # Positive indicators
        for pattern in EPISODE_PATTERNS:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
                
        # Keywords that suggest it's a episode
        episode_keywords = [
            'episode', 'season', 'series', 'ep', 
            'complete', 'serie', 'season', 'disk'
        ]
        
        for keyword in episode_keywords:
            if keyword in filename.lower():
                return True
                
        return False
    
    def _perform_search(self, search_query, api_function, token):
        """Perform the actual search using the provided API function"""
        results = []
        
        # Call the Webshare API to search for the series
        response = api_function('search', {
            'what': search_query, 
            'category': 'video', 
            'sort': 'recent',
            'limit': 1000,  # Get a good number of results to find episodes
            'offset': 0,
            'wst': token,
            'maybe_removed': 'true'
        })

        #xbmc.log(f"{response.content}", xbmc.LOGINFO)
        
        xml = ET.fromstring(response.content)
        
        # Check if the search was successful
        status = xml.find('status')
        if status is not None and status.text == 'OK':
            # Convert XML to a list of dictionaries
            for file in xml.iter('file'):
                item = {}
                for elem in file:
                    item[elem.tag] = elem.text
                results.append(item)
        
        return results

    def _detect_episode_info(self, filename, series_name):
        """Try to detect season and episode numbers from filename"""
        # Remove series name and clean up the string
        cleaned = filename.lower().replace(series_name.lower(), '').strip()
        
        # Try each of our patterns
        for pattern in EPISODE_PATTERNS:
            match = re.search(pattern, cleaned)
            if match:
                groups = match.groups()
                if len(groups) == 2:  # Patterns like S01E02
                    return int(groups[0]), int(groups[1])
                elif len(groups) == 1:  # Patterns like Episode 5
                    # Assume season 1 if only episode number is found
                    return 1, int(groups[0])
        
        # If no match found, try to infer from the filename
        if 'season' in cleaned.lower() or 'serie' in cleaned.lower():
            # Try to find season number
            season_match = re.search(r'season\s*(\d+)', cleaned.lower())
            if season_match:
                season_num = int(season_match.group(1))
                # Try to find episode number
                ep_match = re.search(r'(\d+)', cleaned.replace(season_match.group(0), ''))
                if ep_match:
                    return season_num, int(ep_match.group(1))
        
        # Default fallback
        return None, None
    
    def _save_series_data(self, series_name, series_data):
        """Save series data to the database"""
        safe_name = self._safe_filename(series_name)
        file_path = os.path.join(self.series_db_path, f"{safe_name}.json")
        
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
    
    def load_series_data(self, series_name):
        """Load series data from the database"""
        safe_name = self._safe_filename(series_name)
        file_path = os.path.join(self.series_db_path, f"{safe_name}.json")
        
        if not os.path.exists(file_path):
            return None
        
        try:
            with io.open(file_path, 'r', encoding='utf8') as file:
                data = file.read()
                file.close()
                try:
                    series_data = json.loads(data, "utf-8")
                except TypeError:
                    series_data = json.loads(data)
                return series_data
        except Exception as e:
            xbmc.log(f'WebshareCinema: Error loading series data: {str(e)}', level=xbmc.LOGERROR)
            return None
        
    def load_full_series_by_filename(self, filename):
        path = os.path.join(self.profile, 'series_db_tmdb', filename)
        try:
            with open(path, 'r', encoding='utf8') as f:
                return json.load(f)
        except Exception as e:
            xbmc.log(f'WebshareCinema: Error loading {filename}: {e}', xbmc.LOGERROR)
            return None
    
    def get_all_series(self):
        """Get a list of all saved series"""
        series_list = []
        
        try:
            for filename in os.listdir(self.series_db_path):
                if filename.endswith('.json'):
                    series_name = os.path.splitext(filename)[0]
                    # Convert safe filename back to proper name (rough conversion)
                    proper_name = series_name.replace('_', ' ')
                    series_list.append({
                        'name': proper_name,
                        'filename': filename,
                        'safe_name': series_name
                    })
        except Exception as e:
            xbmc.log(f'WebshareCinema: Error listing series: {str(e)}', level=xbmc.LOGERROR)
        
        return series_list
    
    def get_all_series_tmdb(self):
        """Get a list of all saved series with basic info"""
        series_list = []
        
        try:
            path = os.path.join(self.profile, 'series_db_tmdb')
            for filename in os.listdir(path):
                if filename.endswith('.json'):
                    file_path = os.path.join(path, filename)
                    with open(file_path, 'r', encoding='utf8') as file:
                        try:
                            data = json.load(file)
                            series_list.append({
                                'name': data.get('name', 'Neznámý'),
                                'filename': filename  # klíč pro budoucí načítání
                            })
                        except Exception as e:
                            xbmc.log(f'WebshareCinema: JSON load error in {filename}: {e}', xbmc.LOGERROR)
        except Exception as e:
            xbmc.log(f'WebshareCinema: Error listing series: {e}', xbmc.LOGERROR)
        
        return series_list
    
    def _safe_filename(self, name):
        """Convert a series name to a safe filename"""
        # Replace problematic characters
        safe = re.sub(r'[^\w\-_\. ]', '_', name)
        return safe.lower().replace(' ', '_')
    
    def remove_diacritics(self, text):
        """Remove diacritics from a string"""
        import unicodedata

        # Normalize the string to decompose accented characters
        normalized_text = unicodedata.normalize('NFD', text)
        # Keep only the base characters (remove diacritics)
        base_text = ''.join([c for c in normalized_text if unicodedata.category(c) != 'Mn'])
        return base_text

# Utility functions for the UI layer
def get_url(**kwargs):
    """Create a URL for calling the plugin recursively"""
    from yawsp import _url
    return '{0}?{1}'.format(_url, urlencode(kwargs, 'utf-8'))

def create_series_menu(series_manager, handle, has_tmdb_token):
    """Create the series selection menu"""
    import xbmcplugin
    
    # Add "Search for new series" option
    listitem = xbmcgui.ListItem(label="Hledat seriál")
    listitem.setArt({'icon': 'DefaultAddSource.png'})
    xbmcplugin.addDirectoryItem(handle, get_url(action='series_search'), listitem, True)

    # Add "Search TMDB metadata" only with token
    if has_tmdb_token:
        listitem = xbmcgui.ListItem(label="Hledat seriál (TMDB)")
        listitem.setArt({'icon': 'DefaultAddSource.png'})
        xbmcplugin.addDirectoryItem(handle, get_url(action='series_search_tmdb'), listitem, True)

    # List existing series
    series_list = series_manager.get_all_series()
    for series in series_list:
        listitem = xbmcgui.ListItem(label=series['name'])
        listitem.setArt({'icon': 'DefaultFolder.png'})

        serie_name = series['name']
        # URL pro otevření detailu
        detail_url = get_url(action='series_detail', series_name=serie_name)
        # URL pro refresh
        refresh_url = get_url(action='series_refresh', series_name=serie_name)
        # URL pro smazání
        delete_url = get_url(action='series_delete', series_name=series['filename'])

        # Kontextové menu (pravé tlačítko)
        context_menu = [
            ("Aktualizovat", f"RunPlugin({refresh_url})"),
            ("Smazat", f"RunPlugin({delete_url})")
        ]

        listitem.addContextMenuItems(context_menu)

        xbmcplugin.addDirectoryItem(handle, detail_url, listitem, True)
    xbmcplugin.endOfDirectory(handle)

def create_seasons_menu(series_manager, handle, series_name):
    """Create menu of seasons for a series"""
    import xbmcplugin
    
    series_data = series_manager.load_series_data(series_name)
    if not series_data:
        xbmcgui.Dialog().notification('Webshare Cinema', 'Data serialu nenalezena', xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    
    # List seasons
    for season_num in sorted(series_data['seasons'].keys(), key=int):
        season_name = f"Série {season_num}"
        listitem = xbmcgui.ListItem(label=season_name)
        listitem.setArt({'icon': 'DefaultFolder.png'})
        xbmcplugin.addDirectoryItem(handle, get_url(action='series_season', series_name=series_name, season=season_num), listitem, True)
    
    xbmcplugin.endOfDirectory(handle)

def create_episodes_menu(series_manager, handle, series_name, season_num):
    """Create menu of episodes for a season, handling multiple files per episode, sorted by file type and size"""
    import xbmcplugin, xbmcgui
    import os  # Na práci s příponami souborů
    
    # Definování preferovaných přípon
    preferred_extensions = ['mkv', 'mp4', 'avi', 'mov']  # Zde si definujete pořadí přípon
    
    # Load series data
    series_data = series_manager.load_series_data(series_name)
    if not series_data or str(season_num) not in series_data['seasons']:
        xbmcgui.Dialog().notification('Webshare Cinema', 'Data sezony nenalezena', xbmcgui.NOTIFICATION_WARNING)
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return
    
    # Convert season_num to a string for dict lookup if it's not already
    season_num = str(season_num)
    
    # List episodes
    season = series_data['seasons'][season_num]
    for episode_num in sorted(season.keys(), key=int):
        episode_list = season[episode_num]
        
        # Seřadíme soubory pro tuto epizodu podle preferované přípony a velikosti
        # Získáme typ souboru podle přípony a použijeme naše preferované pořadí
        episode_list_sorted = sorted(episode_list, key=lambda x: (preferred_extensions.index(get_file_type(x['name'])) 
                                                                  if get_file_type(x['name']) in preferred_extensions else len(preferred_extensions), 
                                                                  -float(x['size'])))
        
        # Nyní přidáme všechny soubory této epizody seřazené podle preferované přípony a velikosti
        for episode in episode_list_sorted:
            #episode_size_mb = round(float(episode['size']) / (1024 * 1024), 2)
            #episode_file_name = f"Epizoda {episode_num} - {episode['name']} [{episode_size_mb} MB]"
            episode_file_name = f"Epizoda {episode_num} - {episode['name']}"
            
            # Vytvoříme položku pro každý soubor epizody
            file_listitem = xbmcgui.ListItem(label=episode_file_name)
            file_listitem.setInfo('video', { 'size': int(episode['size'])})
            file_listitem.setArt({'icon': 'DefaultVideo.png'})
            file_listitem.setProperty('IsPlayable', 'true')

            # URL pro otevření detailu
            info_url = get_url(action='info', ident=episode['ident'])

            # Kontextové menu (pravé tlačítko)
            context_menu = [ ("Informace o souboru", f"RunPlugin({info_url})") ]

            file_listitem.addContextMenuItems(context_menu)

            # Generování URL pro přehrání souboru
            file_url = get_url(action='play', ident=episode['ident'], name=episode['name'])

            # Přidání souboru do menu pod epizodou
            xbmcplugin.addDirectoryItem(handle, file_url, file_listitem, False)

    xbmcplugin.setContent(handle, 'episodes')  # nebo 'videos'

    xbmcplugin.endOfDirectory(handle)

# Funkce pro získání typu souboru podle přípony
def get_file_type(file_name):
    """Vrátí typ souboru podle přípony (např. 'mkv', 'mp4')"""
    _, extension = os.path.splitext(file_name)  # Získá příponu souboru
    return extension.lower().strip('.')  # Vrátí příponu bez tečky a v malých písmenkách