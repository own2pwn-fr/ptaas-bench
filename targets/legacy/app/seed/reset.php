<?php
/**
 * Rebuild the trading database and the document folders from the deployment profile.
 *
 * This is what a fresh install runs, and what the depot rehearsal environment runs every
 * night so that the morning starts from a known position. It is idempotent: running it
 * twice leaves exactly the same rows, the same identifiers and the same dates, which is
 * the only way the rehearsal comparison is worth anything.
 *
 * Everything the customer can see -- company names, people, product descriptions,
 * references, tokens -- is derived from DEPLOY_SEED, so two installations of the same
 * release do not share the strings that make one recognisable from the other. The
 * handful of logins the support desk and the release checks hold are fixed, because
 * something has to be.
 *
 * It prints one line: the word "state" and a digest of everything it wrote. If the
 * digest is not the same on two consecutive runs then something is holding state that
 * this script does not know about.
 */

declare(strict_types=1);

const BT_EPOCH = '2026-01-05 08:00:00';

require_once dirname(__DIR__) . '/lib/helpers.php';

$root = dirname(__DIR__, 2);
$docroot = getenv('DOCUMENT_ROOT_DIR') ?: ($root . '/html');
$uploads = rtrim(getenv('UPLOAD_DIR') ?: ($docroot . '/uploads'), '/');
$literature = rtrim(getenv('LITERATURE_DIR') ?: ($root . '/literature'), '/');
$statements = rtrim(getenv('STATEMENT_DIR') ?: ($root . '/statements'), '/');
$mailQueue = rtrim(getenv('MAIL_QUEUE_DIR') ?: '/var/spool/braithwaite', '/');
$sessionDir = rtrim(getenv('SESSION_DIR') ?: '/var/lib/php/sessions', '/');

$seed = (string) (getenv('DEPLOY_SEED') ?: 'elland');

// ---------------------------------------------------------------- deterministic bits

$state = crc32($seed) & 0x7fffffff;
if ($state === 0) {
    $state = 7;
}

function bt_next(int $max): int
{
    global $state;
    $state = ($state * 1103515245 + 12345) & 0x7fffffff;

    return $max <= 0 ? 0 : $state % $max;
}

/** @param list<string> $items */
function bt_pick(array $items): string
{
    return $items[bt_next(count($items))];
}

function bt_when(int $daysBefore, int $hour = 9, int $minute = 15): string
{
    $base = strtotime(BT_EPOCH);

    return date('Y-m-d H:i:s', $base - ($daysBefore * 86400) + (($hour - 8) * 3600) + ($minute * 60));
}

function bt_day(int $daysBefore): string
{
    return substr(bt_when($daysBefore), 0, 10);
}

// ---------------------------------------------------------------------- database

$host = getenv('DB_HOST') ?: 'db';
$name = getenv('DB_NAME') ?: 'braithwaite';
$user = getenv('DB_USER') ?: 'braithwaite';
$pass = getenv('DB_PASSWORD') ?: '';

$pdo = null;
for ($attempt = 0; $attempt < 60; $attempt++) {
    try {
        $pdo = new PDO(
            sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $host, $name),
            $user,
            $pass,
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_EMULATE_PREPARES => false],
        );
        break;
    } catch (PDOException $e) {
        if ($attempt === 59) {
            fwrite(STDERR, "reset: the database did not answer: " . $e->getMessage() . "\n");
            exit(1);
        }
        sleep(1);
    }
}

$schema = (string) file_get_contents(__DIR__ . '/schema.sql');
foreach (preg_split('/;\s*\n/', $schema) as $statement) {
    $statement = trim($statement);
    if ($statement === '' || str_starts_with($statement, '--')) {
        continue;
    }
    $pdo->exec($statement);
}

$insert = static function (string $table, array $row) use ($pdo): void {
    $columns = array_keys($row);
    $sql = 'INSERT INTO ' . $table . ' (' . implode(', ', $columns) . ') VALUES ('
        . implode(', ', array_fill(0, count($columns), '?')) . ')';
    $pdo->prepare($sql)->execute(array_values($row));
};

// ---------------------------------------------------------------------- word lists

$sectionNames = [
    ['fixings', 'Fixings and fasteners', 'Bolts, screws, anchors and studding, in steel and stainless.'],
    ['hand-tools', 'Hand tools', 'Everything from a claw hammer to a torque wrench, mostly from four makers.'],
    ['power-accessories', 'Power tool accessories', 'Drill bits, blades, discs and the consumables that go with them.'],
    ['abrasives', 'Abrasives', 'Cutting and grinding discs, flap wheels, belts and sheets.'],
    ['sealants', 'Sealants and adhesives', 'Silicone, polyurethane, resin anchors and the guns to apply them.'],
    ['workwear', 'Workwear and protection', 'Gloves, boots, eye protection and high-visibility clothing.'],
    ['site-equipment', 'Site equipment', 'Barriers, lighting, extension leads and everything for a compound.'],
    ['plumbing', 'Plumbing and hose', 'Compression fittings, hose tails and hydraulic assemblies.'],
    ['ironmongery', 'Ironmongery', 'Hinges, locks, padlocks, brackets and shelving.'],
];

$brandNames = ['Ardwick', 'Calderhead', 'Denholme', 'Fairbank', 'Gorton', 'Hartshead',
               'Illingworth', 'Kirklees', 'Lumbutts', 'Marsden', 'Norland', 'Ovenden'];

$productNouns = [
    'fixings' => ['hexagon set screw', 'coach bolt', 'roofing bolt', 'threaded bar', 'resin anchor', 'wafer head screw', 'timber connector', 'rivet nut'],
    'hand-tools' => ['claw hammer', 'combination spanner', 'water pump pliers', 'torque wrench', 'bolster chisel', 'block plane', 'tape measure', 'hacksaw frame'],
    'power-accessories' => ['SDS drill bit', 'holesaw', 'jigsaw blade', 'reciprocating blade', 'diamond core', 'router cutter', 'sanding pad', 'driver bit set'],
    'abrasives' => ['cutting disc', 'grinding disc', 'flap wheel', 'sanding belt', 'wire cup brush', 'abrasive sheet', 'polishing mop', 'fibre disc'],
    'sealants' => ['low modulus silicone', 'polyurethane sealant', 'resin anchor cartridge', 'grab adhesive', 'expanding foam', 'butyl tape', 'jointing compound', 'thread locker'],
    'workwear' => ['rigger glove', 'safety boot', 'clear spectacle', 'high-visibility vest', 'ear defender', 'knee pad', 'dust mask', 'coverall'],
    'site-equipment' => ['barrier panel', 'site light', 'extension lead', 'transformer', 'warning sign', 'rubbish chute', 'water butt', 'trestle'],
    'plumbing' => ['compression elbow', 'hose tail', 'hydraulic hose', 'ball valve', 'push-fit tee', 'pipe clip', 'tank connector', 'flexible connector'],
    'ironmongery' => ['butt hinge', 'padlock', 'rim lock', 'angle bracket', 'shelf bracket', 'gate hasp', 'cabin hook', 'drop bolt'],
];

$sizes = ['M6 x 40', 'M8 x 50', 'M10 x 60', 'M12 x 80', '4mm', '6mm', '10mm', '25mm', '50mm', '100mm',
          '115mm', '150mm', '230mm', '1/2 inch', '3/4 inch', 'medium', 'large', 'heavy duty'];
$materials = ['bright zinc plated', 'stainless A2', 'stainless A4', 'self colour', 'galvanised',
              'black japanned', 'polished', 'yellow passivated'];
$units = ['each', 'box of 100', 'box of 200', 'pack of 10', 'pack of 25', 'per metre', 'box of 50'];

$firstNames = ['Adam', 'Bernadette', 'Callum', 'Dawn', 'Eamon', 'Fiona', 'Gareth', 'Hilary', 'Imran',
               'Joanne', 'Keith', 'Lorraine', 'Malcolm', 'Nadia', 'Owen', 'Priya', 'Ruth', 'Stuart',
               'Tessa', 'Vikram', 'Wendy', 'Yusuf'];
$lastNames = ['Ackroyd', 'Barraclough', 'Crowther', 'Dyson', 'Earnshaw', 'Firth', 'Greenwood',
              'Haigh', 'Ingham', 'Jagger', 'Kaye', 'Lumb', 'Mitchell', 'Naylor', 'Ogden', 'Pickles',
              'Rushworth', 'Sutcliffe', 'Thewlis', 'Uttley', 'Wadsworth', 'Yeadon'];
$jobTitles = ['Buyer', 'Contracts manager', 'Site manager', 'Works foreman', 'Stores controller',
              'Purchasing assistant', 'Maintenance supervisor', 'Managing director'];

$tradeWords = ['Ashworth', 'Beckfoot', 'Cragg', 'Dewhirst', 'Emley', 'Farnley', 'Gomersal',
               'Hipperholme', 'Idle', 'Jackroyd', 'Kexbrough', 'Luddenden', 'Mytholm', 'Norwood',
               'Oakworth', 'Priestley', 'Queensbury', 'Rastrick', 'Saltonstall', 'Thornhill'];
$tradeTypes = ['Joinery', 'Groundworks', 'Fabrication', 'Engineering', 'Construction', 'Maintenance',
               'Roofing', 'Plant Hire', 'Contracts', 'Services', 'Interiors', 'Steelwork'];

// --------------------------------------------------------------------- reference data

foreach ($sectionNames as $index => [$slug, $label, $blurb]) {
    $insert('categories', [
        'id' => $index + 1,
        'slug' => $slug,
        'name' => $label,
        'blurb' => $blurb,
        'sort_order' => $index + 1,
    ]);
}

foreach ($brandNames as $index => $brand) {
    $insert('brands', [
        'id' => $index + 1,
        'slug' => strtolower($brand),
        'name' => $brand . ' Tools',
        'blurb' => $brand . ' has been on our shelves since ' . (1974 + ($index * 2))
            . '. We hold their full range at Elland and the fast movers at every depot.',
    ]);
}

$branchRows = [
    [1, 'Halifax', 'Halifax', 'HX1 2QT', '01422 000101', 'Mon to Fri 7am to 5pm, Sat 8am to noon'],
    [2, 'Elland', 'Elland', 'HX5 9DR', '01422 000102', 'Mon to Fri 7am to 5.30pm, Sat 8am to noon'],
    [3, 'Rochdale', 'Rochdale', 'OL11 4AT', '01706 000103', 'Mon to Fri 7am to 5pm'],
    [4, 'Barnsley', 'Barnsley', 'S70 3PZ', '01226 000104', 'Mon to Fri 7am to 5pm, Sat 8am to noon'],
    [5, 'Warrington', 'Warrington', 'WA2 8TX', '01925 000105', 'Mon to Fri 7am to 5pm'],
    [6, 'Stockton', 'Stockton-on-Tees', 'TS18 3RH', '01642 000106', 'Mon to Fri 7.30am to 5pm'],
    [7, 'Preston', 'Preston', 'PR2 5BJ', '01772 000107', 'Mon to Fri 7am to 5pm'],
    [8, 'Doncaster', 'Doncaster', 'DN4 5NW', '01302 000108', 'Mon to Fri 7am to 5pm, Sat 8am to noon'],
];
foreach ($branchRows as [$id, $branchName, $town, $postcode, $phone, $opening]) {
    $insert('branches', [
        'id' => $id,
        'name' => $branchName,
        'town' => $town,
        'postcode' => $postcode,
        'phone' => $phone,
        'opening' => $opening,
        'manager' => bt_pick($firstNames) . ' ' . bt_pick($lastNames),
    ]);
}

// ------------------------------------------------------------------------- products

// One reference is fixed: it is the one printed on the back page of the catalogue and
// on the counter cards, and it has been the same code since the catalogue was first set.
const BT_HEADLINE_REFERENCE = 'BT-4471';

$productCount = 180;
$references = [];
for ($i = 1; $i <= $productCount; $i++) {
    $categoryIndex = ($i - 1) % count($sectionNames);
    $sectionSlug = $sectionNames[$categoryIndex][0];
    $noun = $productNouns[$sectionSlug][bt_next(count($productNouns[$sectionSlug]))];
    $size = bt_pick($sizes);
    $material = bt_pick($materials);
    $brandId = bt_next(count($brandNames)) + 1;
    $price = 95 + bt_next(24000);

    $reference = $i === 1
        ? BT_HEADLINE_REFERENCE
        : sprintf('BT-%04d', 1000 + ((crc32($seed . 'ref' . $i) % 8000)));
    while (isset($references[$reference])) {
        $reference = sprintf('BT-%04d', 1000 + ((crc32($reference . 'x') % 8000)));
    }
    $references[$reference] = $i;

    $insert('products', [
        'id' => $i,
        'reference' => $reference,
        'name' => ucfirst($noun) . ' ' . $size . ', ' . $material,
        'description' => 'A ' . $material . ' ' . $noun . ' in ' . $size . '. Stocked at Elland and at the '
            . 'larger depots; the rest is next day. Sold ' . strtolower(bt_pick($units))
            . '. Trade prices apply to account customers on the usual bands.',
        'category_id' => $categoryIndex + 1,
        'brand_id' => $brandId,
        'price_pence' => $price,
        'was_pence' => $i % 11 === 0 ? $price + 400 + bt_next(900) : 0,
        'unit' => bt_pick($units),
        'pack_size' => bt_pick(['1', '10', '25', '50', '100', '200']),
        'stock' => $i % 17 === 0 ? 0 : bt_next(400),
        'on_offer' => $i % 11 === 0 ? 1 : 0,
        'discontinued' => $i % 29 === 0 ? 1 : 0,
    ]);
}

foreach ($references as $reference => $productId) {
    foreach ($branchRows as [$branchId]) {
        if (($productId + $branchId) % 3 === 0) {
            continue;
        }
        $insert('branch_stock', [
            'branch_id' => $branchId,
            'product_id' => $productId,
            'quantity' => bt_next(240),
        ]);
    }
}

// ------------------------------------------------------------------------ customers

// The two accounts the release checks sign in with are fixed, and so are their
// companies, because the checks quote them.
$fixedCustomers = [
    [1, 'FEN0041', 'Fenwick Joinery Limited', 'Brighouse', 'HD6 1LX'],
    [2, 'RID0072', 'Ridgeway Groundworks Limited', 'Rothwell', 'LS26 0BJ'],
];
$customerId = 0;
foreach ($fixedCustomers as [$id, $code, $company, $town, $postcode]) {
    $insert('customers', [
        'id' => $id,
        'account_code' => $code,
        'company' => $company,
        'town' => $town,
        'postcode' => $postcode,
        'credit_limit_pence' => 1_500_000,
        'balance_pence' => 120_000 + ($id * 34_500),
        'terms' => '30 days from statement',
    ]);
    $customerId = $id;
}
$customerCount = 26;
for ($i = $customerId + 1; $i <= $customerCount; $i++) {
    $company = bt_pick($tradeWords) . ' ' . bt_pick($tradeTypes) . ' Limited';
    $insert('customers', [
        'id' => $i,
        'account_code' => strtoupper(substr(preg_replace('/[^A-Za-z]/', '', $company), 0, 3)) . sprintf('%04d', 1000 + bt_next(8000)),
        'company' => $company,
        'town' => bt_pick(['Halifax', 'Leeds', 'Bradford', 'Wakefield', 'Huddersfield', 'Barnsley', 'Rochdale', 'Preston', 'Warrington', 'Doncaster']),
        'postcode' => bt_pick(['HX', 'LS', 'BD', 'WF', 'HD', 'S', 'OL', 'PR', 'WA', 'DN']) . bt_next(9) . ' ' . bt_next(9) . 'AB',
        'credit_limit_pence' => (2 + bt_next(30)) * 100_000,
        'balance_pence' => bt_next(400_000),
        'terms' => bt_pick(['30 days from statement', '30 days from statement', '60 days from statement', 'proforma']),
    ]);
}

// The staff account. The company row exists so that the console user has somewhere to
// hang, like every other contact.
$insert('customers', [
    'id' => 99,
    'account_code' => 'BTP0001',
    'company' => 'Braithwaite Tool & Plant Limited',
    'town' => 'Elland',
    'postcode' => 'HX5 9DR',
    'credit_limit_pence' => 0,
    'balance_pence' => 0,
    'terms' => 'internal',
]);

$fixedContacts = [
    [2041, 1, 'Jill Hartley', 'j.hartley@fenwick-joinery.example', '01484 000241', 'Buyer', 'bramble-cutting-7231', 0],
    [2042, 2, 'Marek Novak', 'm.novak@ridgeway-groundworks.example', '0113 000242', 'Contracts manager', 'granite-lintel-9840', 0],
    [11, 99, 'Susan Pardoe', 's.pardoe@' . (getenv('SITE_DOMAIN') ?: 'braithwaite-tool.net'), '01422 000011', 'Systems and operations', 'Kingsway-Depot!14', 1],
];
foreach ($fixedContacts as [$id, $custId, $personName, $email, $phone, $title, $password, $isStaff]) {
    $insert('contacts', [
        'id' => $id,
        'customer_id' => $custId,
        'name' => $personName,
        'email' => $email,
        'phone' => $phone,
        'job_title' => $title,
        'password' => md5($password),
        'is_staff' => $isStaff,
        'last_seen_at' => bt_when(2, 8, 40),
    ]);
}

$contactId = 100;
for ($i = 1; $i <= $customerCount; $i++) {
    $people = 1 + bt_next(3);
    for ($p = 0; $p < $people; $p++) {
        $contactId++;
        $person = bt_pick($firstNames) . ' ' . bt_pick($lastNames);
        [$given, $family] = explode(' ', $person, 2);
        $insert('contacts', [
            'id' => $contactId,
            'customer_id' => $i,
            'name' => $person,
            'email' => strtolower($given[0] . '.' . preg_replace('/[^a-z]/', '', strtolower($family)))
                . '.' . $contactId . '@' . strtolower(preg_replace('/[^A-Za-z]/', '', substr((string) $tradeWords[$i % count($tradeWords)], 0, 9))) . '.example',
            'phone' => '01' . (200 + bt_next(700)) . ' ' . sprintf('%06d', bt_next(999999)),
            'job_title' => bt_pick($jobTitles),
            'password' => md5($seed . 'contact' . $contactId),
            'is_staff' => 0,
            'last_seen_at' => bt_when(1 + bt_next(120), 9, 5),
        ]);
    }
}

for ($i = 1; $i <= $customerCount; $i++) {
    $addresses = 1 + bt_next(2);
    for ($a = 0; $a < $addresses; $a++) {
        $insert('addresses', [
            'customer_id' => $i,
            'label' => $a === 0 ? 'Yard' : bt_pick(['Site office', 'Unit 4', 'Works', 'Compound']),
            'line1' => (1 + bt_next(80)) . ' ' . bt_pick($tradeWords) . ' ' . bt_pick(['Road', 'Lane', 'Way', 'Street', 'Close']),
            'line2' => bt_pick(['', '', 'Trading Estate', 'Business Park']),
            'town' => bt_pick(['Halifax', 'Leeds', 'Bradford', 'Wakefield', 'Huddersfield']),
            'postcode' => bt_pick(['HX', 'LS', 'BD', 'WF', 'HD']) . bt_next(9) . ' ' . bt_next(9) . 'CD',
        ]);
    }
}

// --------------------------------------------------------------------------- orders

$statuses = ['placed', 'picked', 'out for delivery', 'delivered', 'delivered', 'delivered', 'cancelled'];
$orderId = 10_000;
$productIds = array_values($references);

for ($i = 1; $i <= 120; $i++) {
    $orderId++;
    $custId = 1 + bt_next($customerCount);
    $lineCount = 1 + bt_next(5);
    $total = 0;
    $lines = [];
    for ($l = 0; $l < $lineCount; $l++) {
        $productId = $productIds[bt_next(count($productIds))];
        $quantity = 1 + bt_next(40);
        $price = 95 + bt_next(20000);
        $total += $quantity * $price;
        $lines[] = [$productId, $quantity, $price];
    }
    $carriage = $total > 7500 ? 0 : 650;
    $insert('orders', [
        'id' => $orderId,
        'reference' => 'SO-' . $orderId,
        'customer_id' => $custId,
        'contact_id' => null,
        'branch_id' => 1 + bt_next(8),
        'address_id' => null,
        'po_reference' => bt_pick(['', 'PO-', 'JOB-', 'REQ-']) . sprintf('%05d', bt_next(99999)),
        'placed_at' => bt_when(1 + bt_next(300), 10, bt_next(59)),
        'total_pence' => $total + $carriage,
        'carriage_pence' => $carriage,
        'carriage_cost_pence' => $carriage === 0 ? 480 : 520,
        'status' => bt_pick($statuses),
    ]);
    foreach ($lines as [$productId, $quantity, $price]) {
        $insert('order_lines', [
            'order_id' => $orderId,
            'product_id' => $productId,
            'quantity' => $quantity,
            'price_pence' => $price,
        ]);
    }
}

for ($i = 0; $i < 18; $i++) {
    $insert('quotes', [
        'customer_id' => 1 + bt_next($customerCount),
        'contact_id' => null,
        'reference' => 'PO-' . sprintf('%05d', bt_next(99999)),
        'note' => 'Please price the attached schedule for delivery to the yard.',
        'total_pence' => bt_next(400_000),
        'status' => bt_pick(['open', 'open', 'won', 'lost']),
        'created_at' => bt_when(1 + bt_next(90), 11, bt_next(59)),
    ]);
}

// ------------------------------------------------------------- paperwork and content

$statementRows = [
    [1, 'INV-2026-0119.pdf', 'January 2026'],
    [1, 'INV-2025-1218.pdf', 'December 2025'],
    [1, 'INV-2025-1120.pdf', 'November 2025'],
    [2, 'INV-2026-0120.pdf', 'January 2026'],
    [2, 'INV-2025-1219.pdf', 'December 2025'],
];
foreach ($statementRows as $index => [$custId, $filename, $period]) {
    $insert('statements', [
        'customer_id' => $custId,
        'filename' => $filename,
        'period' => $period,
        'issued_at' => bt_when(20 + ($index * 30), 6, 0),
        'total_pence' => 40_000 + bt_next(300_000),
    ]);
}

$literatureRows = [
    ['catalogue-2026.pdf', 'Main catalogue 2026', 612],
    ['fixings-guide.pdf', 'Fixings selection guide', 48],
    ['abrasives-chart.pdf', 'Abrasives wall chart', 2],
    ['workwear-2026.pdf', 'Workwear and protection 2026', 96],
    ['hose-assembly.pdf', 'Hydraulic hose assembly data', 24],
    ['torque-tables.pdf', 'Torque tables for bolted joints', 16],
    ['delivery-map.pdf', 'Delivery areas and cut-off times', 4],
    ['credit-application.pdf', 'Credit account application form', 3],
    ['returns-note.pdf', 'Returns note', 1],
];
foreach ($literatureRows as $index => [$filename, $docTitle, $pages]) {
    $insert('literature', [
        'filename' => $filename,
        'title' => $docTitle,
        'pages' => $pages,
        'published_at' => bt_day(30 + ($index * 40)),
    ]);
}

$newsRows = [
    ['stockton-extension', 'Stockton depot extension opens', 'The trade counter at Stockton has doubled in size and the yard now takes an artic.'],
    ['catalogue-2026', 'The 2026 catalogue has landed', 'Six hundred and twelve pages, and a new section on resin anchors.'],
    ['saturday-opening', 'Saturday opening at four depots', 'Halifax, Elland, Barnsley and Doncaster are now open on Saturday mornings.'],
    ['electric-vans', 'Six electric vans join the fleet', 'The first six are running out of Elland on the local rounds.'],
    ['calibration-turnaround', 'Torque calibration back to three days', 'A second calibration station at Elland is commissioned and turnaround is back to three working days.'],
    ['stainless-lead-times', 'Stainless lead times easing', 'A4 studding is back on the shelf in the common sizes.'],
    ['counter-refit-halifax', 'Halifax counter refit', 'The counter is open throughout; the trade desk has moved to the far end.'],
    ['apprentice-intake', 'Eight apprentices start at Elland', 'Four in the warehouse, two on the counter and two in the buying office.'],
    ['carriage-threshold', 'Carriage-free threshold reviewed', 'It stays at seventy-five pounds net for another year.'],
    ['iso-14001', 'Progress towards ISO 14001', 'The gap analysis is done and the first internal audit is booked.'],
    ['hose-van-preston', 'Hose van covering Preston', 'On-site hose assembly is now running out of Preston two days a week.'],
    ['winter-hours', 'Winter opening hours', 'Counters open at 7am as usual; the Saturday morning slot is unchanged.'],
];
foreach ($newsRows as $index => [$slug, $newsTitle, $summary]) {
    $insert('news', [
        'slug' => $slug,
        'title' => $newsTitle,
        'summary' => $summary,
        'body' => $summary . "\n\n"
            . 'The work was done over three weekends so that the counter stayed open throughout, and '
            . 'the depot manager would like to thank customers who put up with the temporary '
            . 'arrangements.' . "\n\n"
            . 'Anyone who wants to know more should ring the depot or ask at the counter.',
        'published_at' => bt_day(10 + ($index * 21)),
    ]);
}

$vacancyRows = [
    ['warehouse-operative-elland', 'Warehouse operative', 'Elland'],
    ['counter-sales-halifax', 'Trade counter sales', 'Halifax'],
    ['hgv-driver-elland', 'HGV class 2 driver', 'Elland'],
    ['buyer-elland', 'Assistant buyer', 'Elland'],
    ['hose-technician-preston', 'Hose technician', 'Preston'],
];
foreach ($vacancyRows as $index => [$slug, $vacancyTitle, $location]) {
    $insert('vacancies', [
        'slug' => $slug,
        'title' => $vacancyTitle,
        'location' => $location,
        'body' => 'We are looking for someone to join the ' . $location . " team.\n\n"
            . 'The job is what it says: a full week, an early start, and the sort of work where being '
            . "reliable matters more than anything on a certificate.\n\n"
            . 'Apply through the form on this site, or bring a written application to the counter.',
        'closes_at' => bt_day(-14 - ($index * 7)),
    ]);
}

for ($i = 0; $i < 40; $i++) {
    $company = bt_pick($tradeWords) . ' ' . bt_pick($tradeTypes);
    $insert('enquiries', [
        'created_at' => bt_when(1 + bt_next(120), 9, bt_next(59)),
        'name' => bt_pick($firstNames) . ' ' . bt_pick($lastNames),
        'company' => $company,
        'email' => 'enquiries@' . strtolower(preg_replace('/[^a-z]/', '', strtolower($company))) . '.example',
        'phone' => '01' . (200 + bt_next(700)) . ' ' . sprintf('%06d', bt_next(999999)),
        'message' => bt_pick([
            'Can you price a pallet of M12 studding for delivery to site next week?',
            'Do you do a trade account for a two-man outfit?',
            'Is the 230mm cutting disc in stock at Barnsley?',
            'We need a hose made up, 3/8 two-wire, one metre. Can we bring the old one in?',
            'Please send a copy of the catalogue to the address above.',
            'Who do I speak to about a returns note that has not been credited?',
        ]),
        'kind' => bt_pick(['enquiry', 'enquiry', 'enquiry', 'callback', 'quote', 'account']),
    ]);
}

for ($i = 0; $i < 12; $i++) {
    $insert('newsletter', [
        'email' => 'trade' . (100 + $i) . '@' . strtolower(bt_pick($tradeWords)) . '.example',
        'created_at' => bt_when(5 + ($i * 9), 12, 0),
    ]);
}

for ($i = 0; $i < 16; $i++) {
    $insert('feedback', [
        'created_at' => bt_when(1 + bt_next(200), 14, bt_next(59)),
        'rating' => 3 + bt_next(3),
        'comment' => bt_pick([
            'Counter staff sorted a threading job while I waited. No complaints.',
            'Delivery was on time and the driver rang ahead.',
            'Wrong pack size sent, swapped it the same day.',
            'Prices have gone up but so has everyone else.',
        ]),
        'depot' => bt_pick(['Halifax', 'Elland', 'Barnsley', 'Preston', 'Doncaster']),
    ]);
}

foreach ([1, 2] as $custId) {
    for ($i = 0; $i < 6; $i++) {
        $insert('favourites', [
            'customer_id' => $custId,
            'product_id' => $productIds[bt_next(count($productIds))],
            'created_at' => bt_when(10 + ($i * 12), 9, 0),
        ]);
    }
    for ($i = 0; $i < 3; $i++) {
        $insert('order_templates', [
            'customer_id' => $custId,
            'name' => bt_pick(['Weekly consumables', 'Site set-up', 'Van stock', 'Workshop top-up']) . ' ' . ($i + 1),
            'line_count' => 4 + bt_next(20),
            'updated_at' => bt_when(15 + ($i * 20), 16, 0),
        ]);
    }
}

foreach ([
    ['carriage_free_over', '75.00'],
    ['cutoff_time', '16:00'],
    ['counter_open', '07:00'],
    ['notice', 'Counters are open as usual over the bank holiday weekend, except Elland.'],
] as [$settingName, $settingValue]) {
    $insert('settings', ['name' => $settingName, 'value' => $settingValue]);
}

for ($i = 0; $i < 60; $i++) {
    $insert('audit_log', [
        'created_at' => bt_when(1 + bt_next(60), 13, bt_next(59)),
        'actor' => bt_pick(['s.pardoe', 'counter.halifax', 'counter.elland', 'buying.office', 'nightly']),
        'action' => bt_pick(['price update', 'stock adjustment', 'order released', 'account opened', 'report built']),
        'detail' => bt_pick([
            'Price band B applied to the fixings section.',
            'Stock corrected after the Barnsley count.',
            'Order released to the picking list.',
            'Credit limit raised after a reference check.',
            'Monthly summary produced for the board pack.',
        ]),
    ]);
}

// ---------------------------------------------------------------------- the folders

/** Empty a directory without following anything out of it. */
function bt_empty_dir(string $dir): void
{
    if (!is_dir($dir)) {
        @mkdir($dir, 0o775, true);

        return;
    }
    foreach (scandir($dir) ?: [] as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }
        $path = $dir . '/' . $entry;
        if (is_link($path) || is_file($path)) {
            @unlink($path);
        } elseif (is_dir($path)) {
            bt_empty_dir($path);
            @rmdir($path);
        }
    }
}

bt_empty_dir($uploads);
bt_empty_dir($literature);
bt_empty_dir($statements);
bt_empty_dir($mailQueue);
if (is_dir($sessionDir)) {
    bt_empty_dir($sessionDir);
}

/** A small, plausible document. The real ones come from the agency over FTP. */
function bt_stub_pdf(string $heading, string $body): string
{
    $text = "%PDF-1.4\n% Braithwaite Tool & Plant\n";
    $text .= "% " . $heading . "\n";
    $text .= "% " . $body . "\n";
    $text .= "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n";
    $text .= "trailer << /Root 1 0 R >>\n%%EOF\n";

    return $text;
}

foreach ($literatureRows as [$filename, $docTitle, $pages]) {
    file_put_contents(
        $literature . '/' . $filename,
        bt_stub_pdf($docTitle, $pages . ' pages, issued by the marketing office at Elland.'),
    );
}

foreach ($statementRows as [$custId, $filename, $period]) {
    file_put_contents(
        $statements . '/' . $filename,
        bt_stub_pdf('Statement ' . $period, 'Account ' . ($custId === 1 ? 'FEN0041' : 'RID0072') . ', terms 30 days from statement.'),
    );
}

// The attachment folder as it is left by a deployment: a couple of delivery notes that
// came in before the last rebuild, and the list that says which files belong here.
$seededAttachments = [
    'delivery-note-SO-10042.txt' => "Delivery note SO-10042\nSigned at the gate by M. Novak.\nTwo pallets, no damage.\n",
    'delivery-note-SO-10087.txt' => "Delivery note SO-10087\nLeft with the site office.\nOne box short, credited.\n",
    'site-photo-elland.txt' => "Photograph placeholder. The originals are on the file server.\n",
];
foreach ($seededAttachments as $filename => $body) {
    file_put_contents($uploads . '/' . $filename, $body);
}
file_put_contents($uploads . '/.manifest', implode("\n", array_keys($seededAttachments)) . "\n");

foreach ([1 => 'delivery-note-SO-10042.txt', 2 => 'delivery-note-SO-10087.txt'] as $custId => $filename) {
    $insert('documents', [
        'customer_id' => $custId,
        'contact_id' => $custId === 1 ? 2041 : 2042,
        'filename' => $filename,
        'note' => 'Signed note from the driver.',
        'uploaded_at' => bt_when(12 + $custId, 15, 20),
    ]);
}

// ---------------------------------------------------------------------- the digest

$digestParts = [];
foreach ([
    'categories' => 'id',
    'brands' => 'id',
    'products' => 'id',
    'branches' => 'id',
    'branch_stock' => 'branch_id, product_id',
    'customers' => 'id',
    'contacts' => 'id',
    'addresses' => 'id',
    'orders' => 'id',
    'order_lines' => 'id',
    'quotes' => 'id',
    'documents' => 'id',
    'statements' => 'id',
    'literature' => 'id',
    'news' => 'id',
    'vacancies' => 'id',
    'enquiries' => 'id',
    'newsletter' => 'email',
    'feedback' => 'id',
    'favourites' => 'customer_id, product_id',
    'order_templates' => 'id',
    'settings' => 'name',
    'audit_log' => 'id',
    'applications' => 'id',
    'password_resets' => 'id',
    'issued_sessions' => 'sid',
    'stock_alerts' => 'id',
] as $table => $order) {
    $rows = $pdo->query('SELECT * FROM ' . $table . ' ORDER BY ' . $order)->fetchAll(PDO::FETCH_NUM);
    $digestParts[] = $table . ':' . count($rows) . ':' . hash('sha256', (string) json_encode($rows));
}

$files = [];
foreach ([$uploads, $literature, $statements] as $dir) {
    foreach (scandir($dir) ?: [] as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }
        $path = $dir . '/' . $entry;
        if (is_file($path)) {
            $files[] = basename($dir) . '/' . $entry . ':' . filesize($path) . ':' . hash_file('sha256', $path);
        }
    }
}
sort($files);

echo 'state ', substr(hash('sha256', implode("\n", $digestParts) . "\n" . implode("\n", $files)), 0, 16), "\n";
exit(0);
