package com.calderwood.meridian.web;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;

/**
 * Serves the console shell for every client-side route.
 *
 * <p>The router owns the paths below; the server only has to hand back the shell so a
 * deep link, a refresh or a bookmark lands somewhere other than a 404.
 */
@Controller
public class SpaController {

    @GetMapping({
            "/sign-in", "/account-recovery", "/forbidden", "/search",
            "/orgs", "/orgs/**",
            "/reports", "/reports/**",
            "/directory", "/directory/**",
            "/tariffs", "/tariffs/**",
            "/intake", "/intake/**",
            "/exports", "/exports/**",
            "/rules", "/rules/**",
            "/approvals", "/approvals/**",
            "/notices", "/notices/**",
            "/notifications", "/notifications/**",
            "/audit", "/audit/**",
            "/imports", "/imports/**",
            "/integrations", "/integrations/**",
            "/settings", "/settings/**"})
    public String shell() {
        return "forward:/index.html";
    }
}
