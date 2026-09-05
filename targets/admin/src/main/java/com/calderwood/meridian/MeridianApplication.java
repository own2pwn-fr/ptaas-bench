package com.calderwood.meridian;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

/** Meridian: the group's operations console. */
@SpringBootApplication
@EnableScheduling
@EnableAsync
public class MeridianApplication {

    public static void main(String[] args) {
        SpringApplication.run(MeridianApplication.class, args);
    }
}
