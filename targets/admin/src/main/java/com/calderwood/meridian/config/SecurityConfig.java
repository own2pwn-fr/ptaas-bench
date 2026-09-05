package com.calderwood.meridian.config;

import com.calderwood.meridian.security.SessionFilter;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

/**
 * Who may reach what.
 *
 * <p>The console is a single-page application served from this origin and its session
 * cookie is strictly same-site, so there is no cross-site form to protect and the
 * framework's token machinery is switched off rather than left half-wired.
 *
 * <p>Path rules are grouped by screen, in the order the screens were built, which is
 * also the order operations think about them.
 */
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http, SessionFilter sessions) throws Exception {
        http
                .csrf(csrf -> csrf.disable())
                .cors(cors -> cors.disable())
                .sessionManagement(session ->
                        session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .httpBasic(basic -> basic.disable())
                .formLogin(form -> form.disable())
                .anonymous(anonymous -> {
                })
                .authorizeHttpRequests(authorize -> authorize
                        // The console shell, its assets and the files a browser asks for
                        // before anyone has signed in.
                        .requestMatchers(HttpMethod.GET,
                                "/", "/index.html", "/favicon.ico", "/robots.txt",
                                "/manifest.webmanifest", "/assets/**", "/.well-known/**",
                                "/*.js", "/*.css", "/*.map", "/chunk-*.js").permitAll()
                        // Anything that is not an API path is a client-side route; the
                        // shell is served for all of them.
                        .requestMatchers(HttpMethod.GET, "/sign-in", "/account-recovery",
                                "/forbidden", "/search", "/orgs/**", "/reports/**", "/directory/**",
                                "/tariffs/**", "/intake/**", "/exports/**", "/rules/**",
                                "/approvals/**", "/notices/**", "/notifications/**", "/audit/**",
                                "/imports/**", "/integrations/**", "/settings/**").permitAll()

                        // Operational endpoints. The management port was meant to stay on
                        // the internal network, which is where the exposure widening
                        // during the memory investigation came from.
                        .requestMatchers("/actuator/**").permitAll()

                        // Sign-in, recovery and the published verification key.
                        .requestMatchers("/api/auth/**").permitAll()
                        // The shell asks who it is before it can decide what to paint.
                        .requestMatchers(HttpMethod.GET, "/api/session/context").permitAll()
                        // The banner has to render on the sign-in screen too.
                        .requestMatchers(HttpMethod.GET, "/api/notices").permitAll()
                        // Render diagnostics and page metrics arrive from browsers that
                        // may have been signed out by the time they fire.
                        .requestMatchers(HttpMethod.POST, "/api/client/**").permitAll()
                        // The saved grid layout is painted before sign-in, from the copy
                        // the browser keeps.
                        .requestMatchers(HttpMethod.GET, "/api/workspace/layout").permitAll()
                        .requestMatchers(HttpMethod.GET, "/api/status").permitAll()

                        // Administrator-only screens.
                        .requestMatchers("/api/integrations/**").hasRole("ADMINISTRATOR")
                        .requestMatchers("/api/imports/**").hasRole("ADMINISTRATOR")
                        .requestMatchers(HttpMethod.POST, "/api/exports/render").hasRole("ADMINISTRATOR")
                        .requestMatchers(HttpMethod.POST, "/api/intake/manifests").hasRole("ADMINISTRATOR")

                        // Approvals: the queue is readable by anyone signed in, and
                        // deciding one is an administrator's job. The rule below was
                        // written when a decision was a POST onto the approval itself.
                        .requestMatchers(HttpMethod.POST, "/api/approvals/*").hasRole("ADMINISTRATOR")

                        // Authoring a notice or a rule is an analyst's job.
                        .requestMatchers(HttpMethod.POST, "/api/notices").hasAnyRole("ANALYST", "ADMINISTRATOR")
                        .requestMatchers(HttpMethod.POST, "/api/rules/**").hasAnyRole("ANALYST", "ADMINISTRATOR")

                        .anyRequest().authenticated())
                .addFilterBefore(sessions, UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    /**
     * Keep the session filter out of the container's own chain.
     *
     * <p>It is a component, so the framework would otherwise register it as an ordinary
     * servlet filter as well as inside the chain below — and the outer copy runs first,
     * establishes the identity, and then clears it again on the way out, before the
     * chain that needed it has run. The result is a console where every request is
     * anonymous and nothing in the wiring looks wrong.
     */
    @Bean
    public FilterRegistrationBean<SessionFilter> sessionFilterRegistration(SessionFilter filter) {
        FilterRegistrationBean<SessionFilter> registration = new FilterRegistrationBean<>(filter);
        registration.setEnabled(false);
        return registration;
    }

    /**
     * Kept for the staff rows migrated from the pre-directory era.
     *
     * <p>Sign-in goes through the corporate directory; these hashes are what is left of
     * the local password table and are still written when somebody is onboarded, so that
     * the migration can be reversed if the directory project is ever unwound.
     */
    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder(10);
    }
}
