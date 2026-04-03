# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**ai-clipper** — a Spring Boot 4.0.5 application using Java 21. Built with the Maven wrapper (`./mvnw`).

## Build & Test Commands

- **Build**: `./mvnw package`
- **Run**: `./mvnw spring-boot:run`
- **Run all tests**: `./mvnw test`
- **Run a single test class**: `./mvnw test -Dtest=AiClipperApplicationTests`
- **Run a single test method**: `./mvnw test -Dtest=AiClipperApplicationTests#contextLoads`
- **Clean build**: `./mvnw clean package`

## Architecture

- Base package: `codecamp.aiclipper`
- Entry point: `AiClipperApplication` (standard `@SpringBootApplication`)
- Configuration: `src/main/resources/application.properties`
- Tests use JUnit 5 with `@SpringBootTest` for integration testing