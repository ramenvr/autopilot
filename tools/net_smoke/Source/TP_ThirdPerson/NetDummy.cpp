#include "NetDummy.h"

#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Net/UnrealNetwork.h"
#include "UObject/ConstructorHelpers.h"

ANetDummy::ANetDummy()
{
	PrimaryActorTick.bCanEverTick = true;
	bReplicates = true;
	SetReplicatingMovement(true);
	Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
	RootComponent = Body;
	static ConstructorHelpers::FObjectFinder<UStaticMesh> Cube(TEXT("/Engine/BasicShapes/Cube.Cube"));
	if (Cube.Succeeded())
	{
		Body->SetStaticMesh(Cube.Object);
	}
	Body->SetRelativeScale3D(FVector(0.6, 0.6, 1.8));
	Body->SetCollisionProfileName(TEXT("BlockAllDynamic"));
}

void ANetDummy::BeginPlay()
{
	Super::BeginPlay();
	Origin = GetActorLocation();
}

void ANetDummy::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (HasAuthority() && !bDead)
	{
		Walked += DeltaSeconds;
		SetActorLocation(Origin + FVector(0.0, FMath::Sin(Walked) * PatrolDistance, 0.0));
	}
}

void ANetDummy::TakeHit(float Damage)
{
	if (!HasAuthority() || bDead)
	{
		return;
	}
	++HitsTaken;
	Health = FMath::Max(0.f, Health - Damage);
	if (Health <= 0.f)
	{
		bDead = true;
		Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		SetActorRotation(FRotator(0.0, 0.0, 90.0));  // (lies down)
	}
}

void ANetDummy::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);
	DOREPLIFETIME(ANetDummy, Health);
	DOREPLIFETIME(ANetDummy, bDead);
}
