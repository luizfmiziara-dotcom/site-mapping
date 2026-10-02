from __future__ import annotations

import base64
import io
from functools import lru_cache

import requests

import contextily as cx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image
from pyproj import Transformer
from sklearn.neighbors import BallTree

st.set_page_config(page_title="Mapa de proximidade", page_icon="🗼", layout="wide")

EARTH_RADIUS_M = 6_371_008.8

# Bases e logos hospedados no Supabase Storage.
# As URLs assinadas podem ser substituídas por st.secrets sem alterar o restante do app.
DEFAULT_SUPABASE_URL = "https://yyrytggagvfnmimrxace.supabase.co"
URL_SITES = (
    "https://yyrytggagvfnmimrxace.supabase.co/storage/v1/object/sign/atc/sites_atc.xlsx"
    "?token=eyJraWQiOiIyNzcxMmEzNS1iNmJiLTRlYTUtYjlhZS1jN2UzZGE4MjA2MjAiLCJhbGciOiJIUzUxMiJ9.eyJ1cmwiOiJhdGMvc2l0ZXNfYXRjLnhsc3giLCJzY29wZSI6ImRvd25sb2FkIiwiaWF0IjoxNzkwNzEzMjY4LCJleHAiOjE4NTM3ODUyNjh9.4ROPDVmY7Pabe9pp1Ham8KB0e4aj-FsRXAJVigXQI7qN0pb7RM0usS39A7uw-tKxHNsfHEeGabbWK7DkN6ZLgQ"
)
URL_COMPETITORS = (
    "https://yyrytggagvfnmimrxace.supabase.co/storage/v1/object/sign/atc/competidores_anatel.xlsx"
    "?token=eyJraWQiOiIyNzcxMmEzNS1iNmJiLTRlYTUtYjlhZS1jN2UzZGE4MjA2MjAiLCJhbGciOiJIUzUxMiJ9.eyJ1cmwiOiJhdGMvY29tcGV0aWRvcmVzX2FuYXRlbC54bHN4Iiwic2NvcGUiOiJkb3dubG9hZCIsImlhdCI6MTc5MDcxMzIwNSwiZXhwIjoxODUzNzg1MjA1fQ.T4iLEncyHMKjpxbzGgLijRcnkYY8bPKvWEHq65FSmayCSCcqZztsftucRSGRTW5-w9o7SmF20Zl7EAgDZMepKg"
)

LOGO_FILES = {
    "ATC": "ATC.png",
    "AMERICAN TOWER": "ATC.png",
    "CLARO": "CLARO.png",
    "TIM": "TIM.png",
    "VIVO": "VIVO.png",
    "ALGAR": "ALGAR.png",
}
COLORS = {
    "ATC": "#D7194A",
    "CLARO": "#E31937",
    "TIM": "#003B7A",
    "VIVO": "#6A00A8",
    "ALGAR": "#0098D8",
    "OPORTUNIDADE": "#00A651",
}


@st.cache_data(ttl=3600, show_spinner=False)
def download_public_logo(filename: str) -> bytes | None:
    url = f"{DEFAULT_SUPABASE_URL}/storage/v1/object/public/logos/{filename}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None


@lru_cache(maxsize=16)
def logo_array(filename: str) -> np.ndarray | None:
    content = download_public_logo(filename)
    if not content:
        return None
    try:
        with Image.open(io.BytesIO(content)).convert("RGBA") as image:
            image.thumbnail((72, 40), Image.Resampling.LANCZOS)
            return np.asarray(image).copy()
    except Exception:
        return None


def normalize_id(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True).str.upper()


def validate_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    output["Lat"] = pd.to_numeric(output["Lat"], errors="coerce")
    output["Lon"] = pd.to_numeric(output["Lon"], errors="coerce")
    valid = output["Lat"].between(-90, 90) & output["Lon"].between(-180, 180)
    return output.loc[valid].copy()


@st.cache_data(ttl=3600, show_spinner="Baixando e carregando bases do Supabase...")
def load_data():
    site_response = requests.get(URL_SITES, timeout=120)
    competitor_response = requests.get(URL_COMPETITORS, timeout=120)
    site_response.raise_for_status()
    competitor_response.raise_for_status()

    sites = pd.read_excel(
        io.BytesIO(site_response.content),
        dtype={"ID": "string", "Site Name": "string"},
    )
    competitors = pd.read_excel(
        io.BytesIO(competitor_response.content),
        dtype={"ID": "string", "Entidade": "string"},
    )

    if not {"Site Name", "ID", "Lat", "Lon"}.issubset(sites.columns):
        raise ValueError("sites_atc.xlsx precisa conter Site Name, ID, Lat e Lon.")
    if not {"ID", "Lat", "Lon", "Entidade"}.issubset(competitors.columns):
        raise ValueError("competidores_anatel.xlsx precisa conter ID, Lat, Lon e Entidade.")

    sites = validate_coordinates(sites)
    competitors = validate_coordinates(competitors)
    sites["ID_BUSCA"] = normalize_id(sites["ID"])
    sites["SITE_BUSCA"] = normalize_id(sites["Site Name"])
    competitors["ID"] = normalize_id(competitors["ID"])
    competitors["Entidade"] = competitors["Entidade"].astype("string").str.strip().str.upper()
    return sites, competitors


@st.cache_resource(show_spinner="Preparando índices geográficos...")
def build_tree(latitudes: tuple, longitudes: tuple):
    coordinates = np.radians(np.column_stack([latitudes, longitudes]))
    return BallTree(coordinates, metric="haversine")


def search_radius(df: pd.DataFrame, tree: BallTree, latitude: float, longitude: float, radius_m: int):
    point = np.radians([[latitude, longitude]])
    indices, distances = tree.query_radius(
        point,
        r=radius_m / EARTH_RADIUS_M,
        return_distance=True,
        sort_results=True,
    )
    result = df.iloc[indices[0]].copy()
    result["Distancia_m"] = distances[0] * EARTH_RADIUS_M
    return result.sort_values("Distancia_m", ascending=True).reset_index(drop=True)


def build_combined_results(sites_nearby: pd.DataFrame, competitors_nearby: pd.DataFrame):
    atc = sites_nearby[["Site Name", "ID", "Lat", "Lon", "Distancia_m"]].copy()
    atc["Tipo"] = "Site ATC"
    atc["Entidade"] = "ATC"
    atc["Nome"] = atc["Site Name"].astype("string")

    competitors = competitors_nearby[["ID", "Lat", "Lon", "Entidade", "Distancia_m"]].copy()
    competitors["Tipo"] = "Competidor"
    competitors["Nome"] = competitors["ID"].astype("string")

    columns = ["Tipo", "Nome", "ID", "Entidade", "Lat", "Lon", "Distancia_m"]
    return pd.concat([atc[columns], competitors[columns]], ignore_index=True).sort_values(
        "Distancia_m", ascending=True
    ).reset_index(drop=True)


def meter_circle(lon: float, lat: float, radius_m: float, points: int = 220):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(lon, lat)
    angle = np.linspace(0, 2 * np.pi, points)
    return x + radius_m * np.cos(angle), y + radius_m * np.sin(angle)


def add_logo_marker(axis, x, y, entity, label, distance=None):
    filename = LOGO_FILES.get(entity)
    image = logo_array(filename) if filename else None
    if image is not None:
        zoom = 0.72 if entity == "ATC" else 0.62
        axis.add_artist(AnnotationBbox(OffsetImage(image, zoom=zoom), (x, y), frameon=False, zorder=8))
    else:
        axis.scatter(
            x, y, s=90, color=COLORS.get(entity, "#F39C12"),
            edgecolor="white", linewidth=1.5, zorder=8,
        )

    text = label if distance is None else f"{label} | {distance:.0f} m"
    axis.annotate(
        text, (x, y), xytext=(8, -18), textcoords="offset points",
        fontsize=8, color="white", weight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.68,
                  edgecolor="white", linewidth=0.5),
        zorder=10,
    )


def add_opportunity_marker(axis, x, y, label):
    axis.scatter(
        x, y, marker="*", s=360, color=COLORS["OPORTUNIDADE"],
        edgecolor="white", linewidth=1.8, zorder=12,
    )
    axis.annotate(
        label, (x, y), xytext=(10, 10), textcoords="offset points",
        fontsize=9, color="white", weight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#087A3D", alpha=0.88,
                  edgecolor="white", linewidth=0.7),
        zorder=13,
    )


def create_map(center: dict, nearby: pd.DataFrame, radius_m: int) -> bytes:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    center_x, center_y = transformer.transform(center["Lon"], center["Lat"])

    figure, axis = plt.subplots(figsize=(12, 8), dpi=150)
    margin = max(radius_m * 1.12, 650)
    axis.set_xlim(center_x - margin, center_x + margin)
    axis.set_ylim(center_y - margin, center_y + margin)
    st.session_state.pop("map_error", None)

    loaded = False
    first_error = None
    for provider in (cx.providers.Esri.WorldImagery, cx.providers.OpenStreetMap.Mapnik):
        try:
            cx.add_basemap(
                axis, source=provider, attribution=False, zoom="auto", reset_extent=True,
                timeout=30, headers={"User-Agent": "Mozilla/5.0 ATC-Mapa-Proximidade/1.0"},
            )
            loaded = True
            break
        except Exception as error:
            first_error = first_error or error

    if not loaded:
        axis.set_facecolor("#DDE7E9")
        axis.text(
            0.5, 0.5, "Mapa-base indisponível\nVerifique proxy/certificado corporativo",
            transform=axis.transAxes, ha="center", va="center",
        )
        st.session_state["map_error"] = str(first_error)

    circle_x, circle_y = meter_circle(center["Lon"], center["Lat"], radius_m)
    axis.plot(circle_x, circle_y, color="#FFD400", linewidth=2.2, linestyle="--", zorder=6)
    axis.fill(circle_x, circle_y, color="#FFD400", alpha=0.04, zorder=5)

    if center["Tipo"] == "Oportunidade":
        add_opportunity_marker(axis, center_x, center_y, center["Nome"])
    else:
        add_logo_marker(axis, center_x, center_y, "ATC", center["Nome"])

    for row in nearby.itertuples(index=False):
        item_x, item_y = transformer.transform(float(row.Lon), float(row.Lat))
        add_logo_marker(
            axis, item_x, item_y, str(row.Entidade), str(row.Nome), float(row.Distancia_m),
        )
        axis.plot(
            [center_x, item_x], [center_y, item_y],
            color="white", linewidth=0.8, alpha=0.75, zorder=4,
        )

    atc_count = int((nearby["Tipo"] == "Site ATC").sum()) if not nearby.empty else 0
    competitor_count = int((nearby["Tipo"] == "Competidor").sum()) if not nearby.empty else 0
    axis.set_title(
        f"{center['Nome']} | Raio de {radius_m / 1000:g} km | "
        f"{atc_count} site(s) ATC e {competitor_count} competidor(es)",
        fontsize=13, weight="bold", pad=12,
    )
    axis.set_axis_off()
    plt.tight_layout()

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight", pad_inches=0.08)
    plt.close(figure)
    return buffer.getvalue()


def copy_image_button(png: bytes):
    image_b64 = base64.b64encode(png).decode("utf-8")
    components.html(
        f"""
        <button id="copyButton" style="width:100%;height:42px;border:0;border-radius:8px;
          background:#FF4B4B;color:white;font-weight:600;cursor:pointer;font-size:14px;">
          📋 Copiar imagem
        </button>
        <div id="copyStatus" style="margin-top:5px;font:12px Arial;text-align:center;color:#555;"></div>
        <script>
          const button = document.getElementById('copyButton');
          const status = document.getElementById('copyStatus');
          button.addEventListener('click', async () => {{
            try {{
              if (!navigator.clipboard || !window.ClipboardItem) throw new Error('Clipboard não suportado');
              const response = await fetch('data:image/png;base64,{image_b64}');
              const blob = await response.blob();
              await navigator.clipboard.write([new ClipboardItem({{'image/png': blob}})]);
              button.textContent = '✅ Imagem copiada';
              status.textContent = 'Use Ctrl+V para colar.';
              status.style.color = '#16803A';
            }} catch (error) {{
              button.textContent = '❌ Não foi possível copiar';
              status.textContent = 'Permita o clipboard no navegador ou use Baixar PNG.';
              status.style.color = '#B42318';
            }}
          }});
        </script>
        """,
        height=75,
    )


st.title("🗼 Mapa de proximidade e oportunidades")
st.caption("Pesquise um site existente ou informe uma coordenada para mapear oportunidades futuras.")
st.caption("Fonte de dados: Supabase Storage")

try:
    sites, competitors = load_data()
except Exception as error:
    st.error("Não foi possível carregar as bases do Supabase.")
    st.exception(error)
    st.stop()

site_tree = build_tree(tuple(sites["Lat"].to_numpy()), tuple(sites["Lon"].to_numpy()))
competitor_tree = build_tree(tuple(competitors["Lat"].to_numpy()), tuple(competitors["Lon"].to_numpy()))

with st.sidebar:
    st.header("Parâmetros")
    search_mode = st.radio(
        "Origem do ponto",
        options=["Site existente", "Coordenada de oportunidade"],
    )

    site_query = ""
    opportunity_name = "Oportunidade futura"
    latitude = 0.0
    longitude = 0.0

    if search_mode == "Site existente":
        site_query = st.text_input("ID ou nome do site", placeholder="Ex.: 125041 ou SPO100AT")
        include_atc_for_site = st.checkbox(
            "Mostrar também outros sites ATC",
            value=False,
            help="Se marcado, o mapa exibirá outros sites ATC e competidores próximos.",
        )
    else:
        opportunity_name = st.text_input("Nome da oportunidade", value="Oportunidade futura")
        coordinate_1, coordinate_2 = st.columns(2)
        with coordinate_1:
            latitude = st.number_input(
                "Latitude", min_value=-90.0, max_value=90.0,
                value=-23.550520, format="%.6f", step=0.000001,
            )
        with coordinate_2:
            longitude = st.number_input(
                "Longitude", min_value=-180.0, max_value=180.0,
                value=-46.633308, format="%.6f", step=0.000001,
            )
        include_atc_for_site = True
        st.caption("Use graus decimais. Ex.: -23.550520, -46.633308")

    radius_m = st.select_slider(
        "Raio de busca",
        options=[500, 1000, 1500, 2000, 2500, 3000],
        value=1000,
        format_func=lambda value: f"{value / 1000:g} km" if value >= 1000 else f"{value} m",
    )
    max_items = st.select_slider(
        "Quantidade de pontos no mapa",
        options=[1, 2, 3, 5, 10, 15, 20, 30, 50, 100, 9999],
        value=20,
        format_func=lambda value: "Todos" if value == 9999 else str(value),
        help="A seleção combina as duas bases e mantém primeiro os pontos mais próximos.",
    )
    generate = st.button("Gerar mapa", type="primary", use_container_width=True)

if generate:
    if search_mode == "Site existente":
        query = site_query.strip().upper()
        if not query:
            st.warning("Informe o ID ou o nome do site.")
            st.stop()

        match = sites[(sites["ID_BUSCA"] == query) | (sites["SITE_BUSCA"] == query)]
        if match.empty:
            st.error("Site não encontrado na base.")
            suggestions = sites[
                sites["ID_BUSCA"].str.contains(query, na=False)
                | sites["SITE_BUSCA"].str.contains(query, na=False)
            ].head(10)
            if not suggestions.empty:
                st.dataframe(suggestions[["Site Name", "ID", "Lat", "Lon"]], hide_index=True)
            st.stop()

        selected_site = match.iloc[0]
        center = {
            "Tipo": "Site ATC",
            "Nome": str(selected_site["Site Name"]),
            "Lat": float(selected_site["Lat"]),
            "Lon": float(selected_site["Lon"]),
        }
    else:
        center = {
            "Tipo": "Oportunidade",
            "Nome": opportunity_name.strip() or "Oportunidade futura",
            "Lat": float(latitude),
            "Lon": float(longitude),
        }

    competitors_nearby = search_radius(
        competitors, competitor_tree, center["Lat"], center["Lon"], radius_m
    )

    if include_atc_for_site:
        sites_nearby = search_radius(sites, site_tree, center["Lat"], center["Lon"], radius_m)
        if search_mode == "Site existente":
            sites_nearby = sites_nearby[sites_nearby["Distancia_m"] > 0.5].copy()
    else:
        sites_nearby = sites.iloc[0:0].copy()
        sites_nearby["Distancia_m"] = pd.Series(dtype="float64")

    all_nearby = build_combined_results(sites_nearby, competitors_nearby)
    total_in_radius = len(all_nearby)
    nearby = all_nearby if max_items == 9999 else all_nearby.head(max_items).copy()

    with st.spinner("Gerando imagem..."):
        png = create_map(center, nearby, radius_m)

    displayed_atc = int((nearby["Tipo"] == "Site ATC").sum()) if not nearby.empty else 0
    displayed_competitors = int((nearby["Tipo"] == "Competidor").sum()) if not nearby.empty else 0

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Ponto central", center["Nome"])
    metric_2.metric("Raio", f"{radius_m / 1000:g} km")
    metric_3.metric("Encontrados no raio", f"{total_in_radius:,}".replace(",", "."))
    metric_4.metric("Exibidos", f"{len(nearby):,}".replace(",", "."))

    st.caption(
        f"Exibidos no mapa: {displayed_atc} site(s) ATC e "
        f"{displayed_competitors} competidor(es)."
    )
    st.image(png, use_container_width=True)

    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in center["Nome"])
    file_name = f"mapa_{safe_name}_{radius_m}m.png"
    button_1, button_2 = st.columns(2)
    with button_1:
        st.download_button(
            "📥 Baixar imagem PNG", data=png, file_name=file_name,
            mime="image/png", type="primary", use_container_width=True,
        )
    with button_2:
        copy_image_button(png)

    if st.session_state.get("map_error"):
        with st.expander("Diagnóstico do mapa-base"):
            st.code(st.session_state["map_error"])

    if nearby.empty:
        st.info("Nenhum site ATC ou competidor encontrado dentro do raio selecionado.")
    else:
        display = nearby[["Tipo", "Nome", "ID", "Entidade", "Distancia_m", "Lat", "Lon"]].copy()
        display.insert(0, "Ordem", np.arange(1, len(display) + 1))
        display["Distancia_m"] = display["Distancia_m"].round().astype(int)
        st.subheader("Pontos exibidos, do mais próximo para o mais distante")
        st.dataframe(display, hide_index=True, use_container_width=True)
