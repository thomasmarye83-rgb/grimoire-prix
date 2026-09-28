# grimoire-prix

Historique des prix Cardmarket des cartes Magic, reconstruit chaque nuit à partir de [MTGJSON](https://mtgjson.com), pour l'onglet « Cote » de l'appli Grimoire.

- Le calcul : `tools/prices.py`, lancé par `.github/workflows/prices.yml` chaque nuit.
- Les données : branche `data` (remplacée à chaque calcul) — `meta.json`, `latest.json`, `movers.json`, `s/xx.json`.

Le prix d'un jour est le prix tendance Cardmarket de l'impression non foil la moins chère.
