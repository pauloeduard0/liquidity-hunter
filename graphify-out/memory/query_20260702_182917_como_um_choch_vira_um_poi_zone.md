---
type: "query"
date: "2026-07-02T18:29:17.439113+00:00"
question: "como um CHoCH vira um POI zone?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["POIDetector", "InternalStructureDetector", "POIZone", "RTOSweepEvent", "load_dashboard_data()"]
---

# Q: como um CHoCH vira um POI zone?

## Answer

Expanded from original query via vocab: [choch, poi, zone, bos, order, block, detector, window, extreme, rto]. Traversal: InternalStructureDetector (internal_structure.py L239) emite CHANGE_OF_CHARACTER/BREAK_OF_STRUCTURE como MarketStructure events; load_dashboard_data (dashboard_data.py L347) chama POIDetector (poi.py L107), que shares_data_with InternalStructureDetector [EXTRACTED] e consome seus structure events + candles. POIDetector.detect() ancora cada zona na janela CHoCH -> primeiro BOS na mesma direcao via ._create_zone(); a candle extrema da janela define os limites do POIZone (poi_zone.py L12). ._update_zone()/._update_bullish()/._update_bearish() gerenciam o lifecycle ACTIVE -> MITIGATED (sweep + close de volta = RTOSweepEvent, poi_zone.py L47) ou ACTIVE -> INVALIDATED (closes persistentes alem do boundary). POIZone e RTOSweepEvent fluem para DashboardDataResponse e sao renderizados por MainChart.tsx via POIBoxesPrimitive.

## Outcome

- Signal: useful

## Source Nodes

- POIDetector
- InternalStructureDetector
- POIZone
- RTOSweepEvent
- load_dashboard_data()