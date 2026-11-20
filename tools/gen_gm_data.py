#!/usr/bin/env python3
"""
GatherMate (Temucraft): node data from the Temucraft world database instead of an old Wowhead dump.

Every herb, ore vein, gas cloud, fishing school and treasure chest spawned in the database (pooled spawns included,
since any of them can be up) is turned into GatherMate's zone and node ids and written to GatherMate_Data/*Data.lua.
Zones come from WorldMapArea.dbc; zones GatherMate doesn't know (our new maps) go to GatherMate/TemucraftZones.lua
with ids 1000 + the WorldMapArea id, so the map pins and minimap work there too.

Usage:
  gen_gm_data.py --db <world database> --dbc <folder with our WorldMapArea.dbc> [--mysql "mysql -uroot -p..."]
                 [--addon <repo folder, default: this script's repo>]
"""
import argparse, os, re, shlex, struct, subprocess, sys
from collections import defaultdict

KINDS = [  # GatherMate node type, data file, global table
    ('Herb Gathering', 'HerbalismData.lua', 'GatherMateDataHerbDB'),
    ('Mining', 'MiningData.lua', 'GatherMateDataMineDB'),
    ('Extract Gas', 'GasData.lua', 'GatherMateDataGasDB'),
    ('Fishing', 'FishData.lua', 'GatherMateDataFishDB'),
    ('Treasure', 'TreasureData.lua', 'GatherMateDataTreasureDB'),
]


def parse_constants(path):
    """GatherMate's zone sizes {mapFile: (w, h, id)} and node ids {type: {english name: id}} from Constants.lua."""
    s = open(path, encoding='latin-1').read()
    zones = {}
    zd = s[s.index('local zone_data = {'):s.index('-- meta table')]
    for m in re.finditer(r'^\s*(\w+)\s*=\s*\{\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*(\d+)\s*,?\s*\}', zd, re.M):
        zones[m.group(1)] = (float(m.group(2)), float(m.group(3)), int(m.group(4)))
    nodes = {}
    block = s[s.index('local node_ids = {'):]
    block = block[:block.index('\n}\n') if '\n}\n' in block else len(block)]
    cur = None
    for line in block.splitlines():
        m = re.match(r'\s*\["([^"]+)"\]\s*=\s*\{', line)
        if m:
            cur = nodes.setdefault(m.group(1), {})
            continue
        m = re.match(r'\s*\[NL\["([^"]+)"\]\]\s*=\s*(\d+)', line)
        if m and cur is not None:
            cur[m.group(1)] = int(m.group(2))
    return zones, nodes


def world_map_areas(path):
    b = open(path, 'rb').read()
    magic, n, f, size, ss = struct.unpack('<4s4I', b[:20])
    strings = b[20 + n * size:]
    out = []
    for i in range(n):
        o = 20 + i * size
        wid, mapid, area, name_off = struct.unpack_from('<4I', b, o)
        left, right, top, bottom = struct.unpack_from('<4f', b, o + 16)
        name = strings[name_off:strings.index(b'\0', name_off)].decode('utf-8', 'replace')
        if area and left != right and top != bottom:
            out.append({'id': wid, 'map': mapid, 'area': area, 'name': name,
                        'left': left, 'right': right, 'top': top, 'bottom': bottom})
    return out


def query(mysql, db, q):
    out = subprocess.run(shlex.split(mysql) + ['-N', '-B', db, '-e', q], check=True, capture_output=True,
                         text=True).stdout
    return [line.split('\t') for line in out.splitlines() if line]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', required=True)
    ap.add_argument('--dbc', required=True, help='folder with WorldMapArea.dbc (ours, with the new zones)')
    ap.add_argument('--mysql', default='mysql')
    ap.add_argument('--addon', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()

    zones, nodes = parse_constants(os.path.join(a.addon, 'GatherMate', 'Constants.lua'))
    names = {}
    for kind, table in nodes.items():
        for name, nid in table.items():
            names[name.lower()] = (kind, nid)

    wma = world_map_areas(os.path.join(a.dbc, 'WorldMapArea.dbc'))
    by_area = defaultdict(list)
    by_map = defaultdict(list)
    new_zones = {}
    for z in wma:
        if z['name'] in zones:
            z['gm'] = zones[z['name']][2]
        else:
            z['gm'] = 1000 + z['id']
            new_zones[z['name']] = (round(z['left'] - z['right'], 4), round(z['top'] - z['bottom'], 4), z['gm'])
        by_area[(z['map'], z['area'])].append(z)
        by_map[z['map']].append(z)

    def zone_for(mapid, zone, x, y):
        def inside(z):
            return z['right'] <= y <= z['left'] and z['bottom'] <= x <= z['top']
        for z in by_area.get((mapid, zone), []):
            if inside(z):
                return z
        # no zone in the spawn row (or it is a sub-area): the smallest zone box on that map holding the spot
        best = None
        for z in by_map.get(mapid, []):
            if inside(z):
                size = (z['left'] - z['right']) * (z['top'] - z['bottom'])
                if best is None or size < best[0]:
                    best = (size, z)
        return best[1] if best else None

    rows = query(a.mysql, a.db,
                 'SELECT g.map, g.zoneId, g.position_x, g.position_y, t.name FROM gameobject g '
                 'JOIN gameobject_template t ON t.entry = g.id WHERE t.type IN (3, 25)')
    # gas clouds are creatures
    gas = [n.replace("'", "''") for n in nodes.get('Extract Gas', {})]
    if gas:
        idcol = 'id1' if query(a.mysql, a.db, "SHOW COLUMNS FROM creature LIKE 'id1'") else 'id'
        rows += query(a.mysql, a.db,
                      "SELECT c.map, c.zoneId, c.position_x, c.position_y, t.name FROM creature c "
                      "JOIN creature_template t ON t.entry = c.%s WHERE t.name IN ('%s')" % (idcol, "','".join(gas)))
    data = {kind: defaultdict(dict) for kind, _, _ in KINDS}
    outside, count = 0, 0
    for mapid, zone, x, y, name in rows:
        hit = names.get(name.lower())
        if not hit:
            continue
        kind, nid = hit
        z = zone_for(int(mapid), int(zone or 0), float(x), float(y))
        if not z:
            outside += 1
            continue
        px = (z['left'] - float(y)) / (z['left'] - z['right'])
        py = (z['top'] - float(x)) / (z['top'] - z['bottom'])
        coord = int(px * 10000 + 0.5) * 10000 + int(py * 10000 + 0.5)
        data[kind][z['gm']][coord] = nid
        count += 1

    data_dir = os.path.join(a.addon, 'GatherMate_Data')
    for kind, fname, table in KINDS:
        with open(os.path.join(data_dir, fname), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('-- Made by tools/gen_gm_data.py from the Temucraft world database. Do not edit.\n')
            fh.write('%s = {\n' % table)
            for zid in sorted(data[kind]):
                fh.write('\t[%d] = {\n' % zid)
                for coord in sorted(data[kind][zid]):
                    fh.write('\t\t[%d] = %d,\n' % (coord, data[kind][zid][coord]))
                fh.write('\t},\n')
            fh.write('}\n')

    used = {z for kind in data for z in data[kind]}
    with open(os.path.join(a.addon, 'GatherMate', 'TemucraftZones.lua'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('-- Made by tools/gen_gm_data.py from our WorldMapArea.dbc: zones GatherMate does not know,\n')
        fh.write('-- {width, height, zone id} by map file name. Do not edit.\n')
        fh.write('GatherMateTemucraftZones = {\n')
        for name in sorted(new_zones):
            w, h, gid = new_zones[name]
            fh.write('\t["%s"] = {%s, %s, %d},\n' % (name, w, h, gid))
        fh.write('}\n')

    for kind, _, _ in KINDS:
        print('%-15s %6d nodes in %3d zones' % (kind, sum(len(v) for v in data[kind].values()), len(data[kind])))
    print('spawns placed: %d, outside every zone (instances): %d, new zones: %d (%d with nodes)' % (
        count, outside, len(new_zones), len(used & {v[2] for v in new_zones.values()})))


if __name__ == '__main__':
    main()
