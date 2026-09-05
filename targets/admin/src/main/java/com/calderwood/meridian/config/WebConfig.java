package com.calderwood.meridian.config;

import jakarta.servlet.MultipartConfigElement;
import org.springframework.boot.web.servlet.MultipartConfigFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.unit.DataSize;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Serving the shell, its assets and the files uploaded to the console. */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    /**
     * Uploads.
     *
     * <p>Rate-card archives from the larger carriers run to a few tens of megabytes, and
     * a manifest from a terminal system can be larger than anyone expects, so the limits
     * are generous. Everything is written to the container's own scratch directory.
     */
    @Bean
    public MultipartConfigElement multipartConfigElement() {
        MultipartConfigFactory factory = new MultipartConfigFactory();
        factory.setMaxFileSize(DataSize.ofMegabytes(96));
        factory.setMaxRequestSize(DataSize.ofMegabytes(128));
        factory.setFileSizeThreshold(DataSize.ofKilobytes(512));
        return factory.createMultipartConfig();
    }
}
