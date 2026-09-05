<?php
/**
 * Site copy for the pages that are text only.
 *
 * The brochure pages have lived in this array since the content was taken out of the
 * templates. Before that each page was a separate template with the words typed into
 * the middle of the markup, which meant a change to the wording was a change to the
 * layout and the counters were the last to know. Everything here is plain text: the
 * template escapes it on the way out, so no tags and no entities belong in these
 * strings. Order the entries as they appear in the footer, not alphabetically.
 */

declare(strict_types=1);

/**
 * @return array<string, array{title:string, strapline:string, sections:list<array{heading:string, body:list<string>}>}>
 */
function bt_content_pages(): array
{
    return [
        'about' => [
            'title' => 'About Braithwaite Tool & Plant',
            'strapline' => 'A West Yorkshire trade supplier of fixings, hand tools and plant consumables since 1962.',
            'sections' => [
                ['heading' => 'Who we are', 'body' => [
                    'Braithwaite Tool & Plant supplies fixings, hand tools, power tool accessories and plant consumables to the building, engineering and maintenance trades across the north of England. We have been doing much the same job since 1962, and a good number of our customers have held an account with us for more than twenty years. The business is still independent and still owned by the family.',
                    'Around 210 people work for us, split between the central warehouse at Elland and the eight depots. Most of them came to us out of the trades they now serve, which is the main reason a counter can answer a question about a thread form without picking up the telephone.',
                ]],
                ['heading' => 'Who we sell to', 'body' => [
                    'Our customers are builders, groundworks contractors, joinery shops, engineering works, local authorities and facilities teams. We sell to the trade; we do not run a retail shop. Account customers make up the bulk of our turnover, though cash sales over the counter are welcome from anyone with a trade address.',
                    'Orders reach us by telephone, by fax, through this site and in person at the counters. Whichever route is used, the same stock and the same pricing apply.',
                ]],
                ['heading' => 'The warehouse and the fleet', 'body' => [
                    'The central warehouse at Elland holds around 19,000 stock lines over 62,000 square feet, with a bulk fixings hall and a separate store for chemicals. Anything in stock and ordered before 4pm on a working day leaves the same evening for next-day delivery.',
                    'We run our own vehicles rather than leaving everything to carriers: eighteen vans and four curtain-siders working set runs out of the depots each morning. The trade counters open at 7am so that vans can be loaded before the first site starts.',
                ]],
            ],
        ],

        'history' => [
            'title' => 'Our history',
            'strapline' => 'From one shop on Gibbet Street to eight depots and a central warehouse.',
            'sections' => [
                ['heading' => 'The first shop', 'body' => [
                    'Harold Braithwaite opened a tool shop on Gibbet Street in Halifax in 1962, selling hand tools and a short range of fixings to local joiners and builders. The van round started three years later with one Bedford and a list of twelve regular customers. By 1971 the shop had taken the premises next door for a fixings counter.',
                ]],
                ['heading' => 'Growing out of Halifax', 'body' => [
                    'The Rochdale depot opened in 1978 and Barnsley in 1984, both on the Halifax pattern of a counter at the front and stock behind it. In 1989 we bought Kerridge Fastener Supplies of Warrington, which brought us a proper bulk fixings business and our first buying office. Preston followed in 1993, and Stockton-on-Tees in 1996 with the purchase of a small plant consumables merchant on Teesside.',
                ]],
                ['heading' => 'Elland and after', 'body' => [
                    'By the middle of the 1990s stock sat in four buildings and nothing could be found twice. The move into a single central warehouse at Elland was finished in 1998, and head office moved with it. Doncaster, the eighth depot, opened in 2004.',
                    'The printed catalogue has been issued every January since 1974. In 2009 we put it online for the first time so that account customers could see stock and prices without ringing the counter. The printed edition carried on, and still does.',
                ]],
            ],
        ],

        'careers' => [
            'title' => 'Working for us',
            'strapline' => 'Roles at the counters, in the warehouse, on the vans and in the offices at Elland.',
            'sections' => [
                ['heading' => 'What it is like here', 'body' => [
                    'We are not a large company and we do not pretend to be one. People tend to stay: a fair number of the counter managers started as Saturday warehouse hands. Hours are ordinary trade hours, the counters run from 7am to 5pm, and there is a rota for Saturday mornings at four of the depots.',
                    'Pay is reviewed each January. Everyone gets staff purchase terms, a contributory pension after the qualifying period and 24 days holiday rising with service.',
                ]],
                ['heading' => 'The roles we recruit for', 'body' => [
                    'Most of our vacancies are trade counter assistants, warehouse operatives and multi-drop delivery drivers on a category C1 licence. We also take on internal sales staff, purchasing assistants, and hose and calibration technicians for the workshop at Elland. Apprenticeships in warehousing and business administration are offered most years, usually starting in September.',
                ]],
                ['heading' => 'How to apply', 'body' => [
                    'Current vacancies are listed on this site with a short description and the depot they are based at. Use the application form on the vacancy page, or post a curriculum vitae to the personnel office at Elland marked for the attention of the personnel manager. We reply to everyone who applies, though it can take a fortnight in a busy period.',
                ]],
            ],
        ],

        'terms' => [
            'title' => 'Conditions of sale',
            'strapline' => 'The conditions on which we sell goods. They apply to every order unless we have agreed otherwise in writing.',
            'sections' => [
                ['heading' => 'Prices and quotations', 'body' => [
                    'Prices charged are those in force on the day of despatch and are exclusive of value added tax. Written quotations are held for 30 days unless a shorter period is stated on the quotation itself. Where a price depends on a raw material surcharge we say so on the quotation.',
                ]],
                ['heading' => 'Orders and delivery', 'body' => [
                    'An order is accepted when we confirm it, not when it is placed. We may decline an order, or supply part of it, where stock is short. Delivery dates are given in good faith and are not a condition of the contract, but we will always tell the account holder when a promised date has moved.',
                ]],
                ['heading' => 'Title, risk and payment', 'body' => [
                    'Risk in the goods passes on delivery, or on collection from a counter. Title does not pass until the goods have been paid for in full, and until then we may ask for them back and enter the premises where they are held in order to recover them. Invoices on a credit account fall due 30 days from the end of the month of invoice; all other sales are payable on collection or before despatch.',
                ]],
                ['heading' => 'Cancellation', 'body' => [
                    'Stock lines may be cancelled at any time before despatch at no cost. Items obtained specially, or cut, threaded or assembled to a customer specification, cannot be cancelled once work has started and will be charged in full. Cancellation of a hire contract is dealt with under the hire terms.',
                ]],
            ],
        ],

        'privacy' => [
            'title' => 'Privacy',
            'strapline' => 'What we hold, why we hold it and who to speak to about it.',
            'sections' => [
                ['heading' => 'What we hold', 'body' => [
                    'For account customers we hold the trading name and address, the delivery addresses, the names, telephone numbers and email addresses of the people authorised to order, purchase order references, the trading history and the payment record. Where a credit application has been made we also hold the trade and bank references given to us and the outcome of the credit reference search.',
                    'For enquiries made through this site or over the telephone we hold the name, the contact details and what was asked. That is so a member of staff can answer the enquiry, and so that we can look back later at what was quoted and to whom.',
                ]],
                ['heading' => 'Why we hold it', 'body' => [
                    'The information is held so that we can take and fulfil orders, arrange delivery, invoice and collect payment, deal with returns and warranty matters, and keep proper accounting records. We do not sell contact details to anybody. Delivery details are passed to our carriers where a consignment travels on a pallet network rather than on our own vans.',
                ]],
                ['heading' => 'How long, and who to contact', 'body' => [
                    'Accounting records are kept for seven years, as we are obliged to do. Enquiry records that do not become orders are cleared after two years, and applications for employment after twelve months. Anyone who wants to know what we hold, or to have it corrected, should write to the data protection contact at the Elland head office and we will reply inside a month.',
                ]],
            ],
        ],

        'cookies' => [
            'title' => 'Cookies',
            'strapline' => 'The small files this site stores on your machine, and what each one is for.',
            'sections' => [
                ['heading' => 'What the site stores', 'body' => [
                    'The site opens a session record when you first arrive so that the basket, and the account area once you have signed in, follow you from page to page. It is cleared when the browser is closed and it carries no name or address.',
                    'If you change how listings look, such as the number of items to a page, whether prices show with or without tax, or the larger text setting, that choice is saved so the site remembers it next time. The last few products you looked at are kept in the same way, to fill the recently viewed strip at the foot of the catalogue pages.',
                ]],
                ['heading' => 'Refusing them', 'body' => [
                    'Every browser will let these files be refused or cleared, usually under privacy or site settings. The basket and the account area will not work without the session record, but the catalogue can still be read. We do not use anything that follows you on to other websites.',
                ]],
            ],
        ],

        'accessibility' => [
            'title' => 'Accessibility',
            'strapline' => 'We would like the site to be usable by everyone who buys from us.',
            'sections' => [
                ['heading' => 'How the pages are built', 'body' => [
                    'Pages use ordinary headings in order, text that can be resized in the browser without the layout falling apart, and a colour scheme chosen for contrast rather than fashion. Pictures that carry meaning have a text description behind them. There is a larger text setting in the appearance options, and it is remembered between visits.',
                ]],
                ['heading' => 'Keyboard and reading order', 'body' => [
                    'Every link, form field and button can be reached with the tab key, in the order the page reads. Form fields are named properly so that a screen reader announces them. Where a form is refused, the reason is given in words at the top of the page as well as beside the field.',
                ]],
                ['heading' => 'If the site does not suit you', 'body' => [
                    'Some of the older catalogue pages date from 2009 and are not as tidy as the rest; we put them right as we come to them. If any part of the site is hard going, ring the Elland office on 01422 000000 between 7am and 5pm and the sales office will take the order or read out whatever you need. Written comments about the site can be sent to the same address.',
                ]],
            ],
        ],

        'delivery' => [
            'title' => 'Delivery',
            'strapline' => 'Next day on stock lines ordered before 4pm, on our own vans wherever the run reaches.',
            'sections' => [
                ['heading' => 'Next-day delivery', 'body' => [
                    'Stock lines ordered before 4pm on a working day are picked the same evening and delivered the next working day. Orders placed after 4pm are picked the following morning. Where a line is not in stock we say so at the time of ordering and give the date it is due in.',
                ]],
                ['heading' => 'Carriage', 'body' => [
                    'Carriage is free on orders over 75 pounds net to a mainland address on one of our own van runs. Below that a carriage charge is added at the rate shown in the printed catalogue. Pallet lines, lengths over three metres and heavy plant consumables travel on a pallet network and are quoted separately; those are next day to most of the north and two days elsewhere.',
                ]],
                ['heading' => 'Depot collection and delivery windows', 'body' => [
                    'Anything held at Elland can be sent down to a depot overnight for collection from the counter the following morning. Collection orders are held for five working days and then returned to stock.',
                    'We cannot give a timed delivery on a general van run, although the driver will telephone ahead where a site asks for it. Morning or afternoon slots can be arranged on a pallet delivery for an additional charge.',
                ]],
            ],
        ],

        'returns' => [
            'title' => 'Returns',
            'strapline' => 'Fourteen days on stock lines, unused and in the original packaging.',
            'sections' => [
                ['heading' => 'What we take back', 'body' => [
                    'Stock lines can be returned within 14 days of the invoice date provided they are unused, complete and in the original undamaged packaging. Fixings returned loose or in a mixed bag cannot be put back into stock and will not be credited. Items obtained specially to order, and anything cut, threaded or assembled for a customer, are not returnable unless they are faulty.',
                ]],
                ['heading' => 'Getting a returns number', 'body' => [
                    'Ring the depot that supplied the goods, or the sales office at Elland, and ask for a returns number before sending anything back. Write the number on the outside of the parcel and enclose a copy of the delivery note. Goods that arrive without a number cannot be matched to an account and are put to one side until we can trace them.',
                    'A handling charge of fifteen per cent may be applied where goods have been held for more than a week, or where the packaging has been opened. It is never applied where the mistake is ours.',
                ]],
                ['heading' => 'Faulty and damaged goods', 'body' => [
                    'Damage in transit must be noted on the driver\'s paperwork and reported to us within three working days. Faulty goods are replaced or credited once we have seen them, and where a manufacturer\'s warranty applies we handle the claim on the account holder\'s behalf. Carriage is not charged on the return of a faulty item.',
                ]],
            ],
        ],

        'credit-account' => [
            'title' => 'Credit accounts',
            'strapline' => 'Thirty-day terms for established trade customers.',
            'sections' => [
                ['heading' => 'Opening an account', 'body' => [
                    'Ask any counter for a credit application form, or download it from this site. We need the full trading name and address, the registered number where the business is a limited company, two trade references and the bank details. Sole traders and partnerships are asked for the names and home addresses of the principals.',
                    'Applications are dealt with by the accounts office at Elland and normally take five working days, most of which is spent waiting on the references. Goods can be bought on cash terms in the meantime.',
                ]],
                ['heading' => 'Limits and reviews', 'body' => [
                    'A credit limit is set when the account opens, reviewed after six months of trading and annually after that. An increase can be asked for at any time; a short trading history and a large order usually means a payment on account up front rather than a refusal. We may hold deliveries where an account is over its limit or past its terms.',
                ]],
                ['heading' => 'Statements and payment', 'body' => [
                    'Statements go out in the first week of each month and cover the month before. Payment falls due 30 days from the end of the month of invoice. We take bank transfer, direct debit, cheque and card; customers on direct debit are told the amount a week before it is taken.',
                ]],
            ],
        ],

        'trade-account' => [
            'title' => 'Trade accounts',
            'strapline' => 'A trade account is about pricing and identification; a credit account is about paying later.',
            'sections' => [
                ['heading' => 'The difference', 'body' => [
                    'A trade account records who you are, which depot you use and which discount band you sit in, and it can be opened over the counter in a few minutes with proof of a trade address. A credit account adds 30-day terms and needs references and a credit search. Plenty of customers hold a trade account for years and pay by card at the counter without ever asking for credit.',
                ]],
                ['heading' => 'Discount bands', 'body' => [
                    'Discount is set by product group rather than across the board, because the margin on a hand tool and the margin on a box of bolts have never been the same thing. New trade accounts start on band C, move to band B once a quarterly spend is established, and to band A by agreement with the depot manager. The bulk break points in the price list apply on top of the band.',
                ]],
                ['heading' => 'The trade card', 'body' => [
                    'Every named buyer on the account is issued with a card carrying the account number and the home depot. Show it at the counter and the sale is priced and put on the account without anything being written out twice. Lost cards should be reported to the depot so they can be stopped; the account holder remains responsible for purchases made on a card until it is reported.',
                ]],
            ],
        ],

        'hire-terms' => [
            'title' => 'Hire terms',
            'strapline' => 'Short-term hire of small tools and site equipment from the depot counters.',
            'sections' => [
                ['heading' => 'What we hire and what it costs', 'body' => [
                    'We hire small tools and site equipment from the counters: breakers, floor saws, dehumidifiers, transformers, task lighting, pumps and access steps. Rates are quoted daily and weekly, with the weekly rate applying from the fourth day. Consumables such as blades, discs, fuel and lubricant are charged as used and are not part of the rate.',
                ]],
                ['heading' => 'Condition and damage waiver', 'body' => [
                    'Equipment goes out clean, checked and with its current inspection record. It should come back the same way; cleaning and refuelling are charged where they are needed. An optional damage waiver at ten per cent of the hire charge limits the hirer\'s liability for accidental damage, but it does not cover loss, theft or misuse, and it is not insurance.',
                ]],
                ['heading' => 'Off-hire', 'body' => [
                    'Hire runs until the equipment is back at a depot or has been collected by us, and an off-hire number must be obtained by telephone to stop the charge. Quoting that number and the date is the only proof that the hire was stopped. Charges continue where equipment is left on a site without an off-hire number, whatever the date written on the delivery note.',
                ]],
            ],
        ],

        'health-and-safety' => [
            'title' => 'Health and safety',
            'strapline' => 'Our policy in short, and how it works at the counters and in the warehouse.',
            'sections' => [
                ['heading' => 'The policy', 'body' => [
                    'We accept our duties under the Health and Safety at Work etc. Act 1974 and the regulations made under it, and the written policy is reviewed each year by the board. The managing director carries overall responsibility; each depot manager is responsible for their own premises and the warehouse manager for Elland. A copy of the policy and the current employers\' liability certificate is displayed at every site.',
                ]],
                ['heading' => 'People and equipment', 'body' => [
                    'Protective equipment is provided to staff at our own cost: gloves, eye protection, high visibility clothing and safety footwear, replaced when worn rather than on a fixed cycle. Racking is inspected once a year by an outside inspector and daily by the warehouse team. Fork lift and pallet truck operators are certificated, and refresher certification is taken every three years.',
                ]],
                ['heading' => 'Handling and chemicals', 'body' => [
                    'Counter staff are trained in manual handling, and we would far rather two people moved a keg of resin slowly than one moved it quickly. Anything over 25 kilogrammes is marked on the shelf edge and shifted with a trolley or a lift.',
                    'Safety data sheets are held for every chemical we stock, in a folder at each counter and on the product page of this site. A printed copy goes out with the first order of any chemical line.',
                ]],
            ],
        ],

        'accreditations' => [
            'title' => 'Accreditations',
            'strapline' => 'The schemes we hold and what each of them covers.',
            'sections' => [
                ['heading' => 'Quality and contractor schemes', 'body' => [
                    'Our quality management system has been certificated to ISO 9001 since 2003 and covers purchasing, goods-in, storage, the workshop services and despatch from every site. We are registered with CHAS and hold current Constructionline registration, both renewed each year. Certificates can be downloaded from this site, and the sales office will send copies for a tender submission.',
                ]],
                ['heading' => 'The fleet', 'body' => [
                    'The delivery fleet holds Fleet Operator Recognition Scheme silver accreditation, which covers driver training, vehicle maintenance records, fuel and mileage reporting and cyclist safety equipment on the larger vehicles. We are assessed against the standard annually and the report can be supplied to customers who need it for a site entry requirement.',
                ]],
                ['heading' => 'Product approvals', 'body' => [
                    'Where a product carries an approval, that approval belongs to the manufacturer and not to us. Structural bolting assemblies, anchors and lifting equipment are bought only from manufacturers who can produce the paperwork behind them. Certificates and declarations of performance are held with the batch record and travel with the goods if they are asked for at the time of ordering.',
                ]],
            ],
        ],

        'sustainability' => [
            'title' => 'Sustainability',
            'strapline' => 'Practical steps at the depots rather than promises.',
            'sections' => [
                ['heading' => 'Vehicles', 'body' => [
                    'The fleet is renewed on a five-year cycle and every replacement since 2018 has met the current low emission standard, with three electric vans now working the Elland and Halifax town runs. Routes are planned the night before to cut empty running, and a consolidated weekly delivery is offered to customers who order small quantities often. Fuel use per drop is reported monthly to the transport manager.',
                ]],
                ['heading' => 'Packaging and waste', 'body' => [
                    'Shrink wrap has come off palletised fixings where strapping will do the job, and the parcel packing area now uses paper void fill. Cardboard and timber are baled at Elland and collected for recycling, as are waste oil, batteries and toner cartridges from the depots. Reusable stillages run between the warehouse and the counters in place of single-trip cartons.',
                ]],
                ['heading' => 'Buildings', 'body' => [
                    'A 240 kilowatt solar array was fitted on the Elland warehouse roof in 2021 and carries a good part of the site load through the summer. Warehouse and counter lighting has been changed over to LED with movement sensors in the aisles. Heating in the picking hall is zoned, so the bulk fixings hall is no longer warmed for the sake of two people.',
                ]],
            ],
        ],

        'services' => [
            'title' => 'Counter services',
            'strapline' => 'Four services run from the depot counters and the workshop at Elland.',
            'sections' => [
                ['heading' => 'Work we do besides selling boxes', 'body' => [
                    'Not everything can be taken off a shelf in the length or the form a job needs, so four services run alongside the counter trade. Threading and cutting, hydraulic hose assembly, torque tool calibration and key cutting are all done by our own staff on our own equipment. Where a service is not offered at your depot the work is done at Elland and comes down on the overnight van.',
                ]],
                ['heading' => 'Threading and cutting', 'body' => [
                    'Studding, bar and tube cut to length and threaded while you wait, in a range of metric and imperial forms. The threading page lists the sizes and materials held.',
                ]],
                ['heading' => 'Hose assembly and calibration', 'body' => [
                    'Hydraulic hose is made up to order at the counter from bulk hose and fittings, usually in under twenty minutes. Torque wrenches, screwdrivers and multipliers are calibrated in the workshop at Elland against reference equipment with traceable certification, and a certificate is issued for each tool.',
                ]],
                ['heading' => 'Key cutting', 'body' => [
                    'Keys are cut at four depots and padlocks can be supplied keyed alike in sets. The key cutting page has the detail.',
                ]],
            ],
        ],

        'services-threading' => [
            'title' => 'Threading and cutting',
            'strapline' => 'Studding, bar and tube cut and threaded at the counter while you wait.',
            'sections' => [
                ['heading' => 'Sizes and materials', 'body' => [
                    'We cut and thread from stock bar in mild steel, bright drawn mild steel, A2 and A4 stainless and brass. Metric coarse threads run from M5 to M36 and metric fine from M8 to M24; BSW, BSF and UNC are cut to order in the common sizes. Studding is held in three metre lengths in mild steel and stainless and can be cut to any length down to 40 millimetres.',
                ]],
                ['heading' => 'How the work is done', 'body' => [
                    'Short runs are cut and threaded on the counter machines at Halifax, Elland, Warrington and Preston while you wait, usually ten to fifteen minutes for a handful of pieces. Longer runs, and anything above M24, go to the workshop at Elland and come back on the overnight van, which normally means the following morning. Ends are chamfered and each thread is checked with a ring gauge before the work leaves the machine.',
                ]],
                ['heading' => 'Ordering and charges', 'body' => [
                    'Give us the material, the diameter, the finished length, and the thread form and length of thread at each end. Cutting is charged by the cut and threading by the end, at the rates in the price list, with a minimum charge per job. Cut and threaded work is made to your specification and cannot be returned.',
                ]],
            ],
        ],

        'services-hose' => [
            'title' => 'Hydraulic hose assembly',
            'strapline' => 'Hose assemblies made up at the counter to your measurements.',
            'sections' => [
                ['heading' => 'What we hold', 'body' => [
                    'Bulk hose is held in one and two wire braid and in four and six spiral, in bores from a quarter of an inch to one and a quarter inches, with a full range of BSP, JIC, metric and ORFS fittings and the common adaptors. Thermoplastic hose for pressure washers and grease lines is stocked as well. Assemblies are crimped on our own machines to the hose maker\'s crimp data, not to a rule of thumb.',
                ]],
                ['heading' => 'Turnaround', 'body' => [
                    'Most assemblies are made while you wait, typically fifteen to twenty minutes at a counter that is not three deep. Bring the old hose if you still have it; measuring from end fitting to end fitting and getting the orientation right saves a second journey. Assemblies wanted outside counter hours can be ordered for collection first thing, and a breakdown service for account customers runs from Elland until 8pm on weekdays.',
                ]],
                ['heading' => 'Marking and pressure proving', 'body' => [
                    'Every assembly is blown through, capped and marked with the date and a job number so that it can be traced back to the hose batch. Proving at one and a half times working pressure is available on request with a certificate. Hose is a wearing part, and we would always suggest that assemblies on plant in constant use are changed on a planned cycle rather than when they burst.',
                ]],
            ],
        ],

        'services-calibration' => [
            'title' => 'Torque tool calibration',
            'strapline' => 'Torque tools calibrated in the workshop at Elland with a certificate for each tool.',
            'sections' => [
                ['heading' => 'What we calibrate', 'body' => [
                    'Torque wrenches, torque screwdrivers, torque multipliers and dial and electronic torque meters are calibrated at Elland from 1 newton metre to 3,000 newton metres. Tools of any make are accepted, not only those bought from us. Where a tool reads outside tolerance we adjust it and check it again, and we quote for repair or replacement before going any further.',
                ]],
                ['heading' => 'Certificates', 'body' => [
                    'Each tool is checked at five points across its range, and in both directions where it works in both, with a certificate showing the readings taken, the tolerance applied and the reference equipment used. Our reference equipment is calibrated every year against national standards, and its certificate numbers appear on yours. Certificates are kept on file for five years and a copy can be sent out if the original goes astray.',
                ]],
                ['heading' => 'Turnaround and reminders', 'body' => [
                    'Standard turnaround is five working days from receipt at Elland, or two days on the express service for an additional charge. Tools handed in at a depot travel on the overnight van, so allow a day either side of that. We keep the calibration dates for account customers and write to them a month before a tool falls due, which is a good deal easier than finding out during a site inspection.',
                ]],
            ],
        ],

        'services-keycutting' => [
            'title' => 'Key cutting',
            'strapline' => 'Keys cut and padlocks keyed alike at Halifax, Elland, Rochdale and Warrington.',
            'sections' => [
                ['heading' => 'Keys', 'body' => [
                    'Cylinder, mortice, window, cabinet and the common vehicle keys are cut to pattern at four depots while you wait, usually in a couple of minutes. Blanks are held for the main lock makers and for every padlock range we sell. We cannot cut a key that is protected by a patent unless you produce the maker\'s card, and we do not cut from a photograph or a rubbing.',
                ]],
                ['heading' => 'Keyed alike and master keying', 'body' => [
                    'Padlocks from our own stocked ranges can be supplied keyed alike in sets, so that one key opens every lock on a compound, a set of vans or a run of site cabins. Sets are made from a recorded code so that a matching lock can be added later, and the code is held against the account rather than stamped on the lock. Simple master keyed suites for cabinets and cupboards are quoted through the depot; anything larger goes to a locksmith we have worked with in Halifax for years.',
                ]],
                ['heading' => 'Charges', 'body' => [
                    'Cutting is charged per key at the rates on the counter card, with a lower rate on quantities over ten. Keyed alike padlock sets are charged at the list price of the padlocks plus a set-up charge for the code. Keys are cut to the pattern supplied; where a worn original has been copied the copy may need easing in the lock, and we will re-cut once at no charge.',
                ]],
            ],
        ],

        'suppliers' => [
            'title' => 'Selling to us',
            'strapline' => 'How to put a range in front of the buying office at Elland.',
            'sections' => [
                ['heading' => 'What we look for', 'body' => [
                    'We buy from manufacturers, and from national distributors where a manufacturer will not deal direct. We would rather hold a smaller number of suppliers for a long time than a long list changed often. A range has to earn its shelf space: steady quality, stock held in this country, clear technical data, and pricing that lets us hold a sensible counter price. Lines that need explaining at length every time rarely sell for us, whatever the margin looks like.',
                ]],
                ['heading' => 'What we expect', 'body' => [
                    'Suppliers are asked to confirm their delivery performance, to hold a reasonable stock of the fast lines, to give at least three months notice of a price increase, and to support the printed catalogue with artwork and data by the end of September. We ask for confirmation of insurance, of quality certification where the product calls for it, and of the matters covered by our modern slavery statement. Payment is on our standard terms of 60 days from the end of the month unless a settlement discount is agreed.',
                ]],
                ['heading' => 'Getting in touch', 'body' => [
                    'Write to the buying office at the Elland head office for the attention of the relevant buyer, or use the enquiry form on this site and choose the supplier option. Send a price list, a sample where that is practical, and one page saying what the range does that the ones we already hold do not. We look at everything that comes in, but we see representatives by appointment only.',
                ]],
            ],
        ],

        'price-promise' => [
            'title' => 'Price promise',
            'strapline' => 'A like-for-like written quotation on a stocked line will be matched.',
            'sections' => [
                ['heading' => 'The promise', 'body' => [
                    'If you hold a written quotation from another supplier for a line we stock, bring it to the counter or send it to the sales office and we will match it or tell you plainly that we cannot. It applies to identical goods: the same manufacturer, the same part number, the same pack quantity and the same finish. A box of unbranded bolts is not the same article as a box with a mill certificate behind it, and we will not pretend that it is.',
                ]],
                ['heading' => 'What is not covered', 'body' => [
                    'We cannot match clearance and end of line offers, prices that depend on membership of a buying group, auction and marketplace prices, or a price that sits below our own landed cost. Delivered prices are compared against our delivered price with carriage included. Items obtained specially to order are quoted one at a time and fall outside this.',
                ]],
                ['heading' => 'How it is applied', 'body' => [
                    'Bring the quotation, dated within the last 30 days, with the supplier named on the face of it. The counter can apply a match up to the depot manager\'s discretion, and anything beyond that is confirmed by the sales office at Elland, usually the same day. A matched price applies to the order in front of us; it does not by itself move the discount band on the account.',
                ]],
            ],
        ],

        'price-list' => [
            'title' => 'Prices and the price list',
            'strapline' => 'How prices are shown here and in the January catalogue.',
            'sections' => [
                ['heading' => 'How prices are shown', 'body' => [
                    'Prices on this site are shown net of value added tax by default, and there is a setting in the appearance options to show them with tax included. Account customers who have signed in see their own banded price alongside the list price. Prices are per unit unless a pack quantity is shown, and where a line is sold by weight the price is per kilogramme with a piece count given as a guide.',
                ]],
                ['heading' => 'When prices change', 'body' => [
                    'The printed list is issued each January and we hold those prices for the year across most of the range. Fixings, wire and anything else priced off raw material move with the market and are re-priced on this site as they change, which is why the site rather than the printed page is the current position. Account customers are given notice in writing of a general increase.',
                ]],
                ['heading' => 'Break points and quantities', 'body' => [
                    'Quantity breaks are shown on the product page and normally fall at one, five, twenty five and one hundred packs, with a further break at full box or full pallet quantities on the common fixings. Breaks are worked out line by line and not across the whole order. For anything above a pallet, ring the sales office for a quotation rather than working from the list.',
                ]],
            ],
        ],

        'quality' => [
            'title' => 'Quality',
            'strapline' => 'Goods-in inspection, batch records on fasteners, and what happens when we get it wrong.',
            'sections' => [
                ['heading' => 'Goods in', 'body' => [
                    'Everything received at Elland is checked against the order and the delivery note for part number, quantity, finish and marking before it is put away. Fasteners are checked for head marking and, on structural and high tensile lines, against the certificate that came with the consignment. Anything that does not agree is quarantined in a marked bay rather than left on the shelf while somebody looks into it.',
                ]],
                ['heading' => 'Batch traceability', 'body' => [
                    'Structural bolting assemblies, high tensile fasteners, anchors and lifting accessories are held by batch, and the batch reference is printed on the picking note and the invoice. That means a certificate can be produced for a delivery months afterwards, and that a doubtful batch can be traced to every customer who received part of it. Ask for the certificate when you order and it travels with the goods; ask afterwards and it takes a day or two.',
                ]],
                ['heading' => 'Complaints', 'body' => [
                    'A complaint can be made at any counter or to the sales office and is written down the same day with a reference number. The depot manager answers straightforward matters within two working days; anything involving a product fault goes to the quality contact at Elland and on to the manufacturer, and we tell the customer where it has got to instead of waiting until we have an answer. Repeat problems are reviewed at the monthly management meeting, and the supplier is asked to attend when the same fault appears three times.',
                ]],
            ],
        ],

        'environmental' => [
            'title' => 'Environmental policy',
            'strapline' => 'What the policy commits us to, the standard we are working towards and how chemicals are stored.',
            'sections' => [
                ['heading' => 'The policy', 'body' => [
                    'Our environmental policy commits us to meeting the regulations that apply to a distributor, to reducing waste and energy use year on year, and to giving customers the information they need to dispose of what we sell. It is signed by the managing director, reviewed annually and displayed at each depot. Objectives are set against measured figures rather than intentions: fuel per drop, waste to landfill, and electricity per square foot of warehouse.',
                ]],
                ['heading' => 'ISO 14001', 'body' => [
                    'We began working towards ISO 14001 in 2022, starting with a register of the environmental effects of the Elland site and the van runs. The management system is written and running, the internal reviews are finished at Elland and at four of the eight depots, and we expect certification of the whole business within the next reporting year. Progress is set out in the annual statement on this site.',
                ]],
                ['heading' => 'Chemicals and spillage', 'body' => [
                    'Resins, adhesives, solvents, fuels and oils are held in a separate bunded store at Elland and in bunded cabinets at the depots, with quantities kept below the thresholds that apply to each site. Stock is rotated by date and short-dated material is used up in the workshop rather than left to go off. Spill kits are held at every store and every vehicle loading point, and drivers carry a small kit for drum and keg deliveries.',
                ]],
            ],
        ],

        'modern-slavery' => [
            'title' => 'Modern slavery statement',
            'strapline' => 'Our annual statement, what we ask of suppliers, and the confidential reporting line.',
            'sections' => [
                ['heading' => 'The statement', 'body' => [
                    'We publish a statement each year under section 54 of the Modern Slavery Act 2015, covering the steps taken in our own business and in our supply chains. It is approved by the board and signed by the managing director, and the current statement and those of previous years can be downloaded from this site. Our own workforce is directly employed on written contracts; agency staff are used only at Elland for seasonal peaks, and only through agencies that have confirmed their own arrangements to us in writing.',
                ]],
                ['heading' => 'Our suppliers', 'body' => [
                    'Direct suppliers are asked to confirm in writing that they meet the Act and to explain how they satisfy themselves about their own sources. Risk is judged on the country of manufacture and the nature of the work rather than the size of the supplier, which puts hand tools, gloves and imported fasteners at the top of our list. Supplier auditing is carried out on the higher risk categories on a three-year cycle, either by our own buyers or through a shared report we are willing to accept from another distributor.',
                ]],
                ['heading' => 'Raising a concern', 'body' => [
                    'Any member of staff, supplier or customer who suspects forced or compulsory labour anywhere in our supply chain can report it in confidence. The confidential reporting line is answered outside the management chain, and a report can be left without giving a name; the number is displayed at every site and printed in the annual statement. Nobody has ever come to any harm for using it, and nobody will.',
                ]],
            ],
        ],

        'insurance' => [
            'title' => 'Insurance',
            'strapline' => 'The cover we carry and how to get a certificate.',
            'sections' => [
                ['heading' => 'Cover', 'body' => [
                    'We carry public liability cover of ten million pounds and product liability cover of ten million pounds, together with employers\' liability cover at the statutory level. Cover is placed through our brokers in Leeds and renews on 1 October each year. Motor cover for the fleet includes goods in transit on our own vehicles.',
                ]],
                ['heading' => 'Certificates and site requirements', 'body' => [
                    'Copies of the certificates can be downloaded from this site and are reissued after each renewal. Where a main contractor needs to be named, or needs a particular wording, send the requirement to the accounts office at Elland and it will go to the brokers; allow a week for a reply. We cannot sign a document that widens the cover beyond what the policy actually provides, and we say so rather than sign and hope.',
                ]],
                ['heading' => 'What the cover does not do', 'body' => [
                    'Product liability covers the goods we sell as they are supplied. It does not extend to the way goods are used, to design work carried out by others, or to an assembly altered after it has left us. Goods sent on a pallet network are covered by the carrier\'s liability, which is limited by weight, so ask us to arrange additional cover on a high value consignment.',
                ]],
            ],
        ],

        'training' => [
            'title' => 'Training',
            'strapline' => 'Toolbox talks on site, fastener selection sessions, and the training room at Elland.',
            'sections' => [
                ['heading' => 'On your site', 'body' => [
                    'We run short toolbox talks at customer premises, usually twenty minutes at the start of a shift, on subjects such as abrasive wheel safety, working with resin anchors, correct use of a torque wrench, and choosing the right glove for the job. They are given by our own staff or by a manufacturer\'s technical representative, and there is no charge to account customers. Ring the depot to arrange one; we need a fortnight\'s notice and somewhere reasonably quiet to stand.',
                ]],
                ['heading' => 'Fastener selection', 'body' => [
                    'The fastener selection session is a longer piece, about two hours, covering property classes, thread forms, coatings and corrosion, torque and preload, and how to read a mill certificate. It is aimed at buyers, supervisors and anyone who has ordered the wrong thing twice. It can be run at a customer site or at Elland, and the notes are handed out afterwards.',
                ]],
                ['heading' => 'The training room at Elland', 'body' => [
                    'The training room above the counter at Elland seats sixteen and has a projector, a display table of working examples of anchors and fixings, and parking outside. It is used for our own staff development and for manufacturer product days, and account customers are welcome to borrow it for their own meetings when it is free. Tea, coffee and a sandwich lunch can be arranged with a few days notice.',
                ]],
            ],
        ],
    ];
}
