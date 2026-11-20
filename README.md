# GatherMate (Temucraft)

GatherMate 1.25 for WoW 3.3.5a with the node data of the Temucraft world, so the map shows the herbs, ore veins, gas
clouds, fishing schools and treasure chests that are really spawned on our server, our new zones included.

- `GatherMate_Data/*Data.lua` is made from our world database by `tools/gen_gm_data.py` (pooled spawns too: any of
  them can be up). Import it in GatherMate's options (Import Data, GatherMateData).
- `GatherMate/TemucraftZones.lua` gives GatherMate the size of the zones it doesn't know (our new maps), from our
  WorldMapArea.dbc, so pins and the minimap work there and it no longer prints "missing data" for them.
- The data is remade every day by the Temucraft modules-ci "Addon data" workflow. By hand:

      python3 tools/gen_gm_data.py --db <world database> --dbc <folder with our WorldMapArea.dbc> --mysql "mysql -uroot -p..."

GatherMate is by kagaro, Xinhuan and Ammo (see GatherMate/LICENSE.txt). This copy only adds the zone file and the
generated data.
