package com.calderwood.meridian.tariffs;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.CurrentActor;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import java.io.File;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.xpath.XPathConstants;
import javax.xml.xpath.XPathFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.w3c.dom.Document;
import org.w3c.dom.NamedNodeMap;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

/**
 * Carrier tariffs.
 *
 * <p>Tariffs arrive from the carriers as one XML file per quarter and are queried in
 * place rather than imported: the schema changes every time a carrier reprices, and
 * nobody wants to migrate a table for it.
 */
@RestController
@RequestMapping("/api/tariffs")
public class TariffController {

    private final File document;

    public TariffController(@Value("${meridian.data.tariffs:/opt/meridian/data/tariffs.xml}")
                            String path) {
        this.document = new File(path);
    }

    /** Every band the current file defines, for the picker. */
    @GetMapping
    public Map<String, Object> bands() {
        CurrentActor.required();
        List<Map<String, Object>> out = new ArrayList<>();
        try {
            Document parsed = parse();
            NodeList bands = (NodeList) XPathFactory.newInstance().newXPath()
                    .evaluate("/tariffs/bands/band", parsed, XPathConstants.NODESET);
            for (int i = 0; i < bands.getLength(); i++) {
                Node band = bands.item(i);
                Map<String, Object> entry = new LinkedHashMap<>();
                entry.put("code", attribute(band, "code"));
                entry.put("name", childText(band, "name"));
                entry.put("mode", childText(band, "mode"));
                entry.put("validFrom", childText(band, "validFrom"));
                entry.put("validTo", childText(band, "validTo"));
                out.add(entry);
            }
        } catch (Exception unreadable) {
            return Map.of("bands", List.of(), "error", "The tariff file for this quarter is not readable.");
        }
        return Map.of("bands", out, "source", document.getName());
    }

    /**
     * The rates for one band.
     *
     * <p>The band code is the key the carrier quotes on its rate schedule, so it is used
     * as it arrives.
     */
    @GetMapping("/lookup")
    public ResponseEntity<Map<String, Object>> lookup(@RequestParam String band) {
        CurrentActor.required();
        String expression = "/tariffs/bands/band[@code='" + band + "']";
        List<Map<String, Object>> nodes = new ArrayList<>();
        NodeList matched;
        try {
            matched = (NodeList) XPathFactory.newInstance().newXPath()
                    .evaluate(expression, parse(), XPathConstants.NODESET);
        } catch (Exception refused) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "That band could not be read from the tariff file."));
        }

        int outsideRequestedBand = 0;
        String firstOutside = null;
        for (int i = 0; i < matched.getLength(); i++) {
            Node node = matched.item(i);
            nodes.add(describe(node));
            if (!withinBand(node, band)) {
                outsideRequestedBand++;
                if (firstOutside == null) {
                    firstOutside = path(node);
                }
            }
        }

        // A lookup for one band answers with that band. Anything else in the result set
        // came from somewhere the expression was not written to reach.
        if (outsideRequestedBand > 0) {
            Telemetry.signal(Anomalies.TARIFF_NODE_ESCAPE, SignalOptions.payload(band)
                    .withDetail(outsideRequestedBand + " of " + matched.getLength()
                            + " nodes served lie outside the requested band, first at " + firstOutside));
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("band", band);
        body.put("matches", nodes);
        return ResponseEntity.ok(body);
    }

    // ------------------------------------------------------------------ helpers

    private Document parse() throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(false);
        return factory.newDocumentBuilder().parse(document);
    }

    /** True when the node is the requested band, or sits inside it. */
    private static boolean withinBand(Node node, String code) {
        for (Node current = node; current != null; current = current.getParentNode()) {
            if ("band".equals(current.getNodeName())) {
                return code.equals(attribute(current, "code"));
            }
        }
        return false;
    }

    private static String path(Node node) {
        StringBuilder out = new StringBuilder();
        for (Node current = node; current != null && current.getNodeType() == Node.ELEMENT_NODE;
             current = current.getParentNode()) {
            out.insert(0, "/" + current.getNodeName());
        }
        return out.toString();
    }

    private static Map<String, Object> describe(Node node) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("element", node.getNodeName());
        NamedNodeMap attributes = node.getAttributes();
        if (attributes != null) {
            Map<String, String> values = new LinkedHashMap<>();
            for (int i = 0; i < attributes.getLength(); i++) {
                values.put(attributes.item(i).getNodeName(), attributes.item(i).getNodeValue());
            }
            if (!values.isEmpty()) {
                out.put("attributes", values);
            }
        }
        List<Map<String, Object>> children = new ArrayList<>();
        NodeList list = node.getChildNodes();
        for (int i = 0; i < list.getLength(); i++) {
            Node child = list.item(i);
            if (child.getNodeType() == Node.ELEMENT_NODE) {
                children.add(describe(child));
            }
        }
        if (children.isEmpty()) {
            out.put("text", node.getTextContent() == null ? "" : node.getTextContent().trim());
        } else {
            out.put("children", children);
        }
        return out;
    }

    private static String attribute(Node node, String name) {
        NamedNodeMap attributes = node.getAttributes();
        if (attributes == null) {
            return null;
        }
        Node value = attributes.getNamedItem(name);
        return value == null ? null : value.getNodeValue();
    }

    private static String childText(Node node, String name) {
        NodeList children = node.getChildNodes();
        for (int i = 0; i < children.getLength(); i++) {
            Node child = children.item(i);
            if (name.equals(child.getNodeName())) {
                return child.getTextContent() == null ? "" : child.getTextContent().trim();
            }
        }
        return null;
    }
}
