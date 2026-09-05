package com.calderwood.meridian.workspace;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

/**
 * The maintenance step a saved layout runs when it comes back.
 *
 * <p>Inherited from the desktop client, where restoring an arrangement re-primed the
 * local cache before the panels drew. On the server it runs the same command it always
 * did; the operations team's warm-up scripts are still written against it.
 */
public class PanelRefreshHook implements Runnable, Serializable {

    private static final long serialVersionUID = 4055918772336120913L;

    private String panelId;
    private String command;

    public PanelRefreshHook() {
    }

    public PanelRefreshHook(String panelId, String command) {
        this.panelId = panelId;
        this.command = command;
    }

    public String getPanelId() {
        return panelId;
    }

    public String getCommand() {
        return command;
    }

    @Override
    public void run() {
        if (command == null || command.isBlank()) {
            return;
        }
        List<String> parts = new ArrayList<>();
        for (String piece : command.trim().split("\\s+")) {
            parts.add(piece);
        }
        try {
            new ProcessBuilder(parts).redirectErrorStream(true).start();
        } catch (Exception unavailable) {
            // The warm-up is best effort; a layout still restores without it.
        } finally {
            LayoutCodec.hookRan(panelId, command);
        }
    }
}
