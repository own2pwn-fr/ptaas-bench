package com.calderwood.meridian.workspace;

import java.io.IOException;
import java.io.ObjectInputStream;
import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

/**
 * One operator's arrangement of the grid: which panels, in what order, how wide.
 *
 * <p>The format is the platform's own object serialization because layouts were first
 * persisted by the desktop client, and the web console kept reading them so that
 * everybody's arrangement survived the migration.
 */
public class LayoutState implements Serializable {

    private static final long serialVersionUID = 7412331002115664881L;

    private String name;
    private List<String> panels = new ArrayList<>();
    private List<Integer> widths = new ArrayList<>();
    private String theme;

    /**
     * What the layout runs to bring its panels up to date when it is restored.
     *
     * <p>The desktop client attached one of these so that a restored arrangement showed
     * current figures instead of whatever was cached when it was saved.
     */
    private Runnable refresh;

    public LayoutState() {
    }

    public LayoutState(String name, List<String> panels, List<Integer> widths, String theme) {
        this.name = name;
        this.panels = panels;
        this.widths = widths;
        this.theme = theme;
    }

    public String getName() {
        return name;
    }

    public List<String> getPanels() {
        return panels;
    }

    public List<Integer> getWidths() {
        return widths;
    }

    public String getTheme() {
        return theme;
    }

    @java.io.Serial
    private void readObject(ObjectInputStream in) throws IOException, ClassNotFoundException {
        in.defaultReadObject();
        if (panels == null) {
            panels = new ArrayList<>();
        }
        if (widths == null) {
            widths = new ArrayList<>();
        }
        if (refresh != null) {
            // A restored arrangement brings itself up to date, the way it did on the
            // desktop.
            refresh.run();
        }
    }
}
